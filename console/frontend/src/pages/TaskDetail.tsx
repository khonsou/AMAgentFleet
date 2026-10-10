import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router'
import { api, type TaskDetail, type Loop } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import LinkifyText from '@/components/LinkifyText'
import { Panel, PanelBody, PanelHead, InsetRow } from '@/components/console/Panel'
import { StatusPill, loopStatusMeta, Led } from '@/components/console/Status'

function fmtTime(iso?: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

const INPUT_CLS = 'rounded-[2px] border-hairline bg-inset text-ink placeholder:text-ink-3 focus-visible:ring-signal/40'

/** 人交付框：human loop 提交 output（状态语义与 agent 自动捕获一致：delivered → review）。 */
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
          placeholder={`在此撰写你的交付内容（v${version}），提交后 task 进入待验收`}
          className={INPUT_CLS + ' min-h-12 flex-1 text-xs'} />
        <Button size="sm" variant="signal" disabled={busy || !text.trim()} onClick={submit}
          className="h-8 px-3 text-[11px]">
          {busy ? '提交中…' : '提交交付'}
        </Button>
      </div>
      {err && <div className="mt-1 text-[11px] text-led-red">{err}</div>}
    </div>
  )
}

/** 领取面板：开放 task（unassigned）提供领取入口——派给 agent 或人自领。 */
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
      <StatusPill color="amber" blink className="text-led-amber">待领取</StatusPill>
      <select value={pick} onChange={(e) => setPick(e.target.value)}
        className="ctl h-7 rounded-[2px] border border-hairline bg-inset px-2 font-mono text-xs text-ink">
        <option value="">选择领取者…</option>
        <option value="__me__">🧑 我（人工）</option>
        {agents.map((a) => <option key={a.name} value={a.name}>🤖 {a.display_name || a.name}</option>)}
      </select>
      <Button size="sm" variant="signal" disabled={busy || !pick} onClick={claim}
        className="h-7 px-2.5 text-[11px]">认领</Button>
      {err && <span className="text-xs text-led-red">{err}</span>}
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
    const t = setInterval(load, 4000)
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

  const poolMeta = t
    ? t.pool_status === 'review' ? { label: '待验收', led: 'amber' as const }
      : t.pool_status === 'done' ? { label: '已终结', led: 'green' as const }
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
          {t && (
            <>
              <StatusPill color="off">{t.status}</StatusPill>
              {poolMeta && <StatusPill color={poolMeta.led} blink={t.pool_status === 'review'}>{poolMeta.label}</StatusPill>}
              <span className="font-mono text-xs text-ink-3">
                创建 {fmtTime(t.created_at)} · 来源 {t.source}
              </span>
            </>
          )}
        </div>
      </div>

      {err && <div className="mb-4 rounded-[2px] border border-led-red/30 bg-led-red/10 px-4 py-3 text-sm text-led-red">{err}</div>}
      {msg && <div className="mb-4 rounded-[2px] border border-led-green/30 bg-led-green/10 px-4 py-3 text-sm text-led-green">{msg}</div>}

      {t && (
        <>
          <Panel className="mb-5">
            <PanelHead><span className="silk">INPUT&nbsp;/&nbsp;任务输入</span></PanelHead>
            <PanelBody>
              <pre className="lcd whitespace-pre-wrap rounded-[2px] border border-hairline-soft p-3 text-xs leading-relaxed text-ink-2">{t.input}</pre>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                {t.pool_status !== 'done' && (
                  <Button size="sm" variant="destructive" disabled={busy}
                    onClick={() => act(() => api.taskClose(id), '任务已关闭作废')}>
                    关闭作废
                  </Button>
                )}
                {t.agent_name !== 'unassigned' && t.agent_name !== 'human' ? (
                  <Link to={`/agents/${t.agent_name}`} className="link-console inline-flex items-center font-mono text-xs">
                    执行 agent：{t.agent_name} →
                  </Link>
                ) : t.agent_name === 'human' ? (
                  <span className="font-mono text-xs text-ink-2">🧑 人工领取（见下方 loops）</span>
                ) : detail!.loops.length === 0 ? (
                  <ClaimPanel taskId={id} onDone={load} />
                ) : (
                  <span className="font-mono text-xs text-ink-3">人工领取（见下方 loops）</span>
                )}
              </div>
            </PanelBody>
          </Panel>

          <Panel className="mb-5">
            <PanelHead>
              <span className="silk">LOOPS&nbsp;/&nbsp;领取与执行记录</span>
              <span className="silk">{detail!.loops.length}&nbsp;LOOPS</span>
            </PanelHead>
            <PanelBody className="space-y-2.5">
              {detail!.loops.map((l: Loop) => {
                const lm = loopStatusMeta(l.status)
                return (
                  <InsetRow key={l.id} id={`loop-${l.id}`} className="scroll-mt-24">
                    <div className="flex flex-wrap items-center gap-2.5 font-mono text-[11px] text-ink-3">
                      <span className="text-ink">loop #{l.id}</span>
                      <span>{l.claimant}</span>
                      <span>({l.claimant_type === 'human' ? '人' : 'agent'})</span>
                      <StatusPill color={lm.led} blink={lm.blink}>{lm.label} v{l.version}</StatusPill>
                      <span className="ml-auto tabular-nums">{l.tokens ?? '—'} tok · {fmtTime(l.created_at)}</span>
                    </div>
                    {l.deliverable && (
                      <div className="mt-2.5 rounded-[2px] border border-led-green/40 bg-led-green/[0.07] px-3.5 py-3">
                        <div className="silk mb-2 flex items-center gap-2">
                          <Led color="green" />
                          OUTPUT&nbsp;/&nbsp;交付内容
                        </div>
                        <LinkifyText className="block whitespace-pre-wrap text-[13px] leading-relaxed text-ink" text={l.deliverable} />
                      </div>
                    )}
                    {l.error && <div className="mt-1 text-xs text-led-red">{l.error}</div>}
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      {l.thread_id && (
                        <Link to={`/threads/${l.thread_id}`} className="link-console font-mono text-[11px]">
                          会话 {l.thread_id.slice(0, 8)} →
                        </Link>
                      )}
                      {l.claimant_type === 'human' && ['claimed', 'returned'].includes(l.status) && t.pool_status !== 'done' && (
                        <HumanDeliverBox loopId={l.id} version={l.version} onDone={load} />
                      )}
                      {l.claimant_type === 'agent' && ['claimed', 'returned'].includes(l.status) && t.pool_status !== 'done' && (
                        <Button size="sm" variant="outline" disabled={busy}
                          onClick={() => act(() => api.loopRequeue(l.id), `loop #${l.id} 已重派`)}
                          className="h-7 px-2.5 text-[11px]">
                          重新入队
                        </Button>
                      )}
                      {l.status === 'delivered' && t.pool_status === 'review' && (
                        <>
                          <Button size="sm" variant="signal" disabled={busy}
                            onClick={() => act(() => api.taskAccept(id, l.id), `已采纳 loop #${l.id}`)}
                            className="h-7 px-2.5 text-[11px]">
                            采纳
                          </Button>
                          <Textarea value={returnTarget === l.id ? returnText : ''}
                            onChange={(e) => { setReturnTarget(l.id); setReturnText(e.target.value) }}
                            placeholder="退回意见" rows={1}
                            className={INPUT_CLS + ' min-h-7 w-64 text-xs'} />
                          <Button size="sm" variant="outline" disabled={busy || returnTarget !== l.id || !returnText.trim()}
                            onClick={() => act(() => api.loopReturn(l.id, returnText), `已退回 loop #${l.id}`)}
                            className="h-7 border-led-amber/40 px-2.5 text-[11px] text-led-amber hover:bg-led-amber/10 hover:text-led-amber">
                            退回
                          </Button>
                        </>
                      )}
                    </div>
                  </InsetRow>
                )
              })}
              {detail!.loops.length === 0 && (
                <div className="py-6 text-center font-mono text-sm text-ink-3">
                  还没有人领取。回 Fleet 看板对本任务发起领取，或用派发表单指派给 agent。
                </div>
              )}
            </PanelBody>
          </Panel>

          <Panel>
            <PanelHead>
              <span className="silk">COMMENTS&nbsp;/&nbsp;人机共用协议</span>
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
