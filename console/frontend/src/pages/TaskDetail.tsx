import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router'
import { api, type TaskDetail, type Loop } from '@/lib/api'
import { fmtTime, fmtFullTime } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import LinkifyText from '@/components/LinkifyText'
import { Panel, PanelBody, PanelHead, InsetRow } from '@/components/console/Panel'
import { StatusPill } from '@/components/console/Status'
import { loopStatusMeta, taskStatusMeta } from '@/components/console/statusMeta'
import LoopOutput from '@/components/console/LoopOutput'
import CollapsibleContent from '@/components/console/CollapsibleContent'
import ErrorBanner from '@/components/console/ErrorBanner'

const INPUT_CLS = 'rounded-[2px] border-hairline bg-inset text-ink placeholder:text-ink-3 focus-visible:ring-signal/40'

/** 人工 LOOP 提交 OUTPUT，状态与 Agent 自动交付一致：delivered → review。 */
function HumanDeliverBox({ loopId, version, onDone }: { loopId: number; version: number; onDone: () => void }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const submit = async () => {
    if (!text.trim() || busy) return
    setBusy(true); setErr('')
    try { await api.loopDeliver(loopId, text.trim()); setText(''); onDone() }
    catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }

  return (
    <div className="mt-1 w-full">
      <div className="flex items-start gap-2">
        <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={2}
          placeholder={`填写 OUTPUT（v${version}），提交后 Task 进入待验收`}
          className={INPUT_CLS + ' min-h-12 flex-1 text-xs'} />
        <Button size="sm" variant="signal" disabled={busy || !text.trim()} onClick={submit}
          className="h-8 px-3 text-[11px]">{busy ? '提交中…' : '提交交付'}</Button>
      </div>
      {err && <ErrorBanner error={err} className="mt-2" />}
    </div>
  )
}

/** 开放 Task 的领取入口：派给 Agent 或由人工领取。 */
function ClaimPanel({ taskId, onDone }: { taskId: number; onDone: () => void }) {
  const [agents, setAgents] = useState<{ name: string; display_name: string }[]>([])
  const [pick, setPick] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    api.agents().then((list) => setAgents(list.filter((a) => a.status === 'registered' || a.status === 'running')))
      .catch(() => setAgents([]))
  }, [])

  const claim = async () => {
    if (!pick || busy) return
    setBusy(true); setErr('')
    const [type, who] = pick === '__me__' ? ['human', 'me'] : ['agent', pick]
    try { await api.taskClaim(taskId, type, who); onDone() } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <StatusPill color="amber" className="text-led-amber">待领取</StatusPill>
      <select aria-label="选择领取者" value={pick} onChange={(e) => setPick(e.target.value)}
        className="ctl h-7 rounded-[2px] border border-hairline bg-inset px-2 font-mono text-xs text-ink">
        <option value="">选择领取者…</option>
        <option value="__me__">我（人工）</option>
        {agents.map((a) => <option key={a.name} value={a.name}>{a.display_name || a.name}</option>)}
      </select>
      <Button size="sm" variant="signal" disabled={busy || !pick} onClick={claim}
        className="h-7 px-2.5 text-[11px]">领取</Button>
      {err && <ErrorBanner error={err} className="w-full" />}
    </div>
  )
}

/** Task 详情页：任何 task 节点（含排队中、无 thread 的）都可进入。
 *  看输入、loops 执行状况、错误、评论；可做关闭/重派操作。 */
export default function TaskDetailPage() {
  const { tid } = useParams<{ tid: string }>()
  const [detail, setDetail] = useState<TaskDetail | null>(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [returnTarget, setReturnTarget] = useState<number | null>(null)
  const [returnText, setReturnText] = useState('')

  const id = Number(tid)
  const load = () => {
    if (!id) return
    api.taskDetail(id).then(setDetail).catch((e) => setErr(String(e)))
  }

  useEffect(() => {
    load()
    const t = setInterval(load, 15000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  // #loop-N 锚点定位：从 Thread Detail 层级眉标跳入时，滚到对应 loop 卡并短暂高亮
  useEffect(() => {
    const m = window.location.hash.match(/^#loop-(\d+)$/)
    if (!m || !detail) return
    const el = document.getElementById(`loop-${m[1]}`)
    if (!el) return
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    el.style.transition = 'box-shadow .3s'
    el.style.boxShadow = '0 0 0 1.5px hsl(var(--led-amber))'
    const t = setTimeout(() => { el.style.boxShadow = '' }, 2000)
    return () => clearTimeout(t)
  }, [detail])

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true); setErr(''); setMsg('')
    try { await fn(); setMsg(ok); await load() } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }

  const t = detail?.task

  const taskMeta = t ? taskStatusMeta(t.status) : null
  const poolMeta = t
    ? t.pool_status === 'review' ? { label: '待验收', led: 'amber' as const }
      : t.pool_status === 'done'
        ? { label: t.accepted_loop_id ? '已验收' : '已结束', led: t.accepted_loop_id ? 'green' as const : 'off' as const }
        : { label: '开放中', led: 'cyan' as const }
    : null

  return (
    <div className="mx-auto max-w-4xl px-7 pb-20 pt-7">
      <div className="mb-6">
        <Link to="/" className="link-console font-mono text-xs">← FLEET BOARD</Link>
        <div className="silk mt-2">MODULE&nbsp;01&nbsp;/&nbsp;FLEET&nbsp;BOARD</div>
        <div className="mt-2 font-mono text-xs text-ink-3">
          {t?.parent_task_id ? (
            <>
              <Link to={`/tasks/${t.parent_task_id}`} className="link-console">TASK&nbsp;#{t.parent_task_id}</Link>
              <span className="mx-1.5 text-ink-3/50">›</span>
            </>
          ) : null}
          <span className="text-ink-2">TASK&nbsp;#{tid}</span>
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-2.5">
          <h1 className="max-w-[560px] truncate text-[21px] font-semibold tracking-tight"
            title={t?.input || ''}>
            {t?.input?.trim() || 'Task 详情'}
          </h1>
          {t && taskMeta && (
            <>
              <StatusPill color={taskMeta.led} blink={taskMeta.blink}>{taskMeta.label}</StatusPill>
              {poolMeta && <StatusPill color={poolMeta.led}>{poolMeta.label}</StatusPill>}
              <span className="font-mono text-xs text-ink-3" title={fmtFullTime(t.created_at)}>
                创建 {fmtTime(t.created_at)} · 来源 {t.source}
              </span>
            </>
          )}
        </div>
      </div>

      {err && <ErrorBanner error={err} className="mb-4" />}
      {msg && <div className="mb-4 rounded-[2px] border border-led-green/30 bg-led-green/10 px-4 py-3 text-sm text-led-green">{msg}</div>}

      {t && (
        <>
          <Panel className="mb-5">
            <PanelHead><span className="silk">TASK 输入</span></PanelHead>
            <PanelBody>
              <div className="lcd rounded-[2px] border border-hairline-soft p-3">
                <CollapsibleContent text={t.input || ''} className="block whitespace-pre-wrap break-words text-xs leading-relaxed text-ink-2" />
              </div>
              <div className="mt-2 font-mono text-[11px] text-ink-3">
                首选模型：{t.model_profile || '系统／Agent 默认'} · Subagent：{t.subagent_model_policy === 'inherit' ? '继承 Task 模型' : '自动选模'}
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                {t.pool_status !== 'done' && (
                  <Button size="sm" variant="destructive" disabled={busy}
                    onClick={() => act(() => api.taskClose(id), 'Task 已关闭')}>
                    关闭 Task
                  </Button>
                )}
                {t.agent_name !== 'unassigned' && t.agent_name !== 'human' ? (
                  <Link to={`/agents/${t.agent_name}`} className="link-console inline-flex items-center font-mono text-xs">
                    Agent：{t.agent_name} →
                  </Link>
                ) : t.agent_name === 'human' ? (
                  <span className="font-mono text-xs text-ink-2">人工领取 · 见下方 LOOP</span>
                ) : detail!.loops.length === 0 ? (
                  <ClaimPanel taskId={id} onDone={load} />
                ) : (
                  <span className="font-mono text-xs text-ink-3">人工领取 · 见下方 LOOP</span>
                )}
              </div>
            </PanelBody>
          </Panel>

          {detail!.children.length > 0 && (
            <Panel className="mb-5">
              <PanelHead>
                <span className="silk">子 TASK</span>
                <span className="silk">{detail!.children.length}&nbsp;TASKS</span>
              </PanelHead>
              <PanelBody className="space-y-2">
                {detail!.children.map((child) => (
                  <InsetRow key={child.id} className="flex flex-wrap items-center gap-2.5 text-xs">
                    <Link to={`/tasks/${child.id}`} className="link-console font-mono">TASK #{child.id}</Link>
                    <span className="min-w-0 flex-1 truncate text-ink-2" title={child.input}>{child.input}</span>
                    <span className="font-mono text-[11px] text-ink-3">{child.agent_name}</span>
                    <StatusPill color={child.pool_status === 'done' ? 'green' : child.pool_status === 'review' ? 'amber' : 'cyan'}>
                      {child.pool_status === 'done' ? '已验收' : child.pool_status === 'review' ? '待验收' : child.status}
                    </StatusPill>
                  </InsetRow>
                ))}
              </PanelBody>
            </Panel>
          )}

          <Panel className="mb-5">
            <PanelHead>
              <span className="silk">LOOP</span>
              <span className="silk">{detail!.loops.length}&nbsp;LOOPS</span>
            </PanelHead>
            <PanelBody className="space-y-2.5">
              {detail!.loops.map((l: Loop) => {
                const lm = loopStatusMeta(l.status)
                return (
                  <InsetRow key={l.id} id={`loop-${l.id}`} className="scroll-mt-24">
                    <div className="flex flex-wrap items-center gap-2.5 font-mono text-[11px] text-ink-3">
                      <span className="font-semibold text-ink">LOOP #{l.id}</span>
                      <span>{l.claimant} · {l.claimant_type === 'human' ? '人工' : 'Agent'}</span>
                      <StatusPill color={lm.led} blink={lm.blink}>{lm.label} v{l.version}</StatusPill>
                      <span className="ml-auto tabular-nums" title={fmtFullTime(l.created_at)}>{l.tokens ?? '—'} Token · {fmtTime(l.created_at)}</span>
                    </div>
                    {l.claimant_type === 'agent' && (
                      <div className="mt-2 font-mono text-[11px] text-ink-3" title={l.model_snapshot?.model || l.model_profile || ''}>
                        模型 {l.model_snapshot?.display_name || l.model_profile || '系统默认'}
                        {l.model_selected_by && <span> · 由 {l.model_selected_by} 选择</span>}
                      </div>
                    )}
                    {l.deliverable && <LoopOutput l={l} />}
                    {l.error && (
                      <div className="mt-2 rounded-[2px] border border-led-red/20 bg-led-red/5 p-2.5">
                        <div className="flex flex-wrap gap-x-3 gap-y-1 font-mono text-[11px] text-led-red">
                          <span>执行异常 · {l.failure_kind === 'recursion' ? '运行步数耗尽' : l.failure_kind === 'transient' ? '临时故障' : l.failure_kind === 'permanent' ? '需人工处理' : '历史故障'}</span>
                          <span className="text-ink-3">自动重试 {l.retry_count ?? 0}/2 · 断点续跑 {l.continuation_count ?? 0}/3</span>
                          {l.recovery_action && <span className="text-led-amber">已排队：{l.recovery_action === 'resume' ? '断点续跑' : '自动重试'}{l.next_retry_at ? ` · ${fmtTime(l.next_retry_at)}` : ''}</span>}
                          {l.running_action && <span className="text-led-cyan">正在{l.running_action === 'resume' ? '从断点继续' : '重试'}</span>}
                        </div>
                        <ErrorBanner error={l.error} className="mt-2" />
                      </div>
                    )}
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      {l.thread_id && (
                        <Link to={`/threads/${l.thread_id}`} className="link-console font-mono text-[11px]">Thread {l.thread_id.slice(0, 8)} →</Link>
                      )}
                      {l.claimant_type === 'human' && ['claimed', 'returned'].includes(l.status) && t.pool_status !== 'done' && (
                        <HumanDeliverBox loopId={l.id} version={l.version} onDone={load} />
                      )}
                      {l.claimant_type === 'agent' && ['claimed', 'returned'].includes(l.status) && t.pool_status !== 'done' && (
                        <>
                          {l.thread_id && l.error && (
                            <Button size="sm" variant="signal" disabled={busy || Boolean(l.recovery_action) || (l.continuation_count ?? 0) >= 3}
                              onClick={() => act(() => api.loopResume(l.id), `LOOP #${l.id} 已从最近断点继续`)}
                              className="h-7 px-2.5 text-[11px]">从断点继续</Button>
                          )}
                          <Button size="sm" variant="outline" disabled={busy || Boolean(l.recovery_action)}
                            onClick={() => act(() => api.loopRequeue(l.id), `LOOP #${l.id} 已重新执行`)}
                            className="h-7 px-2.5 text-[11px]">重新执行</Button>
                        </>
                      )}
                      {l.status === 'delivered' && t.pool_status === 'review' && (
                        <>
                          <Button size="sm" variant="signal" disabled={busy}
                            onClick={() => act(() => api.taskAccept(id, l.id), `已采纳 LOOP #${l.id} 的交付`)}
                            className="h-7 px-2.5 text-[11px]">采纳交付</Button>
                          <Textarea value={returnTarget === l.id ? returnText : ''}
                            onChange={(e) => { setReturnTarget(l.id); setReturnText(e.target.value) }}
                            placeholder="退回意见" rows={1}
                            className={INPUT_CLS + ' min-h-7 w-64 text-xs'} />
                          <Button size="sm" variant="outline" disabled={busy || returnTarget !== l.id || !returnText.trim()}
                            onClick={() => act(() => api.loopReturn(l.id, returnText), `已退回 LOOP #${l.id}`)}
                            className="h-7 border-led-amber/40 px-2.5 text-[11px] text-led-amber hover:bg-led-amber/10 hover:text-led-amber">退回</Button>
                        </>
                      )}
                    </div>
                  </InsetRow>
                )
              })}
              {detail!.loops.length === 0 && (
                <div className="py-6 text-center text-sm text-ink-3">尚未领取，请在上方选择领取者。</div>
              )}
            </PanelBody>
          </Panel>

          <Panel>
            <PanelHead>
              <span className="silk">COMMENTS</span>
              <span className="silk">{detail!.comments.length}&nbsp;条</span>
            </PanelHead>
            <PanelBody className="space-y-2">
              {detail!.comments.map((c) => (
                <InsetRow key={c.id} className="text-xs">
                  <span className="font-mono text-ink">{c.author}</span>
                  <span className="ml-2 font-mono text-[11px] text-ink-3">{fmtTime(c.created_at)}</span>
                  <LinkifyText className="mt-1 block whitespace-pre-wrap text-ink-2" text={c.content} />
                </InsetRow>
              ))}
              {detail!.comments.length === 0 && <div className="font-mono text-xs text-ink-3">暂无评论</div>}
            </PanelBody>
          </Panel>
        </>
      )}
    </div>
  )
}
