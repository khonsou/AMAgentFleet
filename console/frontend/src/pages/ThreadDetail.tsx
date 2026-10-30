import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router'
import { api, type ThreadDetail, type ChatMessage, type ReviewContext } from '@/lib/api'
import { fmtTime, fmtFullTime } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Panel, PanelBody, PanelHead, InsetRow } from '@/components/console/Panel'
import CollapsibleContent from '@/components/console/CollapsibleContent'
import ErrorBanner from '@/components/console/ErrorBanner'
import { Led, StatusPill } from '@/components/console/Status'
import { runStatusMeta } from '@/components/console/statusMeta'

const INPUT_CLS = 'rounded-[2px] border-hairline bg-inset text-ink placeholder:text-ink-3 focus-visible:ring-signal/40'

/** 网关 /api/threads/{tid}/messages 返回 run_events 事件流：
 *  [{event_type: "llm.human.input"|"llm.ai.response", category: "message",
 *    content: {content: string|parts[], type: "human"|"ai", ...}, seq, ...}]
 * 规整为 {role, content, name} 供对话流渲染。 */
function extractText(c: unknown): string {
  if (typeof c === 'string') return c
  if (Array.isArray(c)) {
    return (c as Record<string, unknown>[])
      .map((p) => (typeof p === 'string' ? p : ((p?.text as string) ?? '')))
      .join('')
  }
  return ''
}

function normalizeMessages(raw: unknown): ChatMessage[] {
  const arr = Array.isArray(raw) ? raw : ((raw as { messages?: unknown[] })?.messages ?? [])
  const out: ChatMessage[] = []
  for (const m of arr as Record<string, unknown>[]) {
    // 事件信封：消息体在 content 字段内，且自身还是 LangChain 序列化消息
    const inner = (m.content && typeof m.content === 'object' ? m.content : m) as Record<string, unknown>
    const type = (inner.type ?? m.event_type ?? '') as string
    const content = extractText(inner.content)
    if (!content.trim()) continue
    const role =
      type === 'human' || type === 'user' || type === 'llm.human.input' ? 'human'
      : type === 'ai' || type === 'assistant' || type === 'llm.ai.response' ? 'ai'
      : type === 'tool' ? 'tool'
      : 'unknown'
    if (role === 'unknown') continue
    const name = (inner.name ?? m.name) as string | undefined
    out.push({ role, content, name })
  }
  return out
}

/** 从消息事件流提取产物文件路径（present_files 呈现 = 用户可下载意图）：
 *  1) run.delivery 事件的 paths/presented_paths/matched_paths
 *  2) ai 消息 tool_calls 中 present_files 的 filepaths
 *  去重保序。 */
function extractArtifacts(raw: unknown): string[] {
  const arr = Array.isArray(raw) ? raw : ((raw as { messages?: unknown[] })?.messages ?? [])
  const paths = new Set<string>()
  const add = (p: unknown) => { if (typeof p === 'string' && p.startsWith('/')) paths.add(p) }
  for (const m of arr as Record<string, unknown>[]) {
    const inner = (m.content && typeof m.content === 'object' ? m.content : m) as Record<string, unknown>
    if ((m.event_type ?? inner.event_type) === 'run.delivery') {
      for (const k of ['paths', 'presented_paths', 'matched_paths']) {
        const v = inner[k]
        if (Array.isArray(v)) v.forEach(add)
      }
    }
    if (Array.isArray(inner.tool_calls)) {
      for (const tc of inner.tool_calls as Record<string, unknown>[]) {
        const args = tc.args as Record<string, unknown> | undefined
        if (tc.name === 'present_files' && Array.isArray(args?.filepaths)) args!.filepaths.forEach(add)
      }
    }
  }
  return [...paths]
}

/** Thread 内验收条：Task 在 review 态时，读对话即可就地采纳／退回。 */
function ReviewBar({ ctx, reload }: { ctx: ReviewContext | null; reload: () => void }) {
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [error, setError] = useState('')

  if (!ctx?.loop || ctx.task?.pool_status !== 'review') return null
  const loop = ctx.loop
  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true); setMsg(''); setError('')
    try { await fn(); setMsg(ok); reload() } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }

  return (
    <div className="mb-4 rounded-[2px] border border-led-amber/30 bg-led-amber/5 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <StatusPill color="amber" className="text-led-amber">Task #{loop.task_id} 待验收</StatusPill>
        <Button size="sm" variant="signal" disabled={busy}
          onClick={() => act(() => api.taskAccept(loop.task_id, loop.id), '已采纳交付，Task 完成')}
          className="h-7 px-3 text-[11px]">采纳交付</Button>
        <Textarea value={comment} onChange={(e) => setComment(e.target.value)}
          rows={2}
          placeholder="退回意见（沿当前 Thread 继续修正）"
          className={INPUT_CLS + ' w-72 resize-y text-xs'} />
        <Button size="sm" variant="outline" disabled={busy || !comment.trim()}
          onClick={() => act(() => api.loopReturn(loop.id, comment), '已退回')}
          className="h-7 border-led-amber/40 px-3 text-[11px] text-led-amber hover:bg-led-amber/10 hover:text-led-amber">退回</Button>
        {msg && <span className="font-mono text-xs text-led-green">{msg}</span>}
      </div>
      {error && <ErrorBanner error={error} className="mt-3" />}
    </div>
  )
}

export default function ThreadDetailPage() {
  const { tid } = useParams<{ tid: string }>()
  const [detail, setDetail] = useState<ThreadDetail | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [files, setFiles] = useState<string[]>([])
  const [rctx, setRctx] = useState<ReviewContext | null>(null)
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [err, setErr] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)

  const load = async () => {
    if (!tid) return
    try {
      const [d, m] = await Promise.all([api.thread(tid), api.messages(tid)])
      setDetail(d)
      setMessages(normalizeMessages(m))
      setFiles(extractArtifacts(m))
      setErr('')
    } catch (e) {
      setErr(String(e))
    }
    // 验收上下文独立容错：thread 不一定挂在任务链上，失败不阻塞页面
    api.reviewContext(tid).then(setRctx).catch(() => setRctx(null))
  }

  useEffect(() => {
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tid])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length, sending])

  const send = async () => {
    if (!tid || !input.trim() || sending) return
    setSending(true)
    setErr('')
    try {
      await api.send(tid, input.trim())
      setInput('')
      await load()
    } catch (e) {
      setErr(String(e))
    } finally {
      setSending(false)
    }
  }

  const t = detail?.thread as Record<string, string> | undefined
  const runMeta = t?.status ? runStatusMeta(t.status) : null
  const firstHuman = messages.find((m) => m.role === 'human')

  return (
    <div className="mx-auto max-w-7xl px-7 pb-20 pt-7">
      <div className="mb-6 flex items-end justify-between">
        <div>
          <Link to="/threads" className="link-console font-mono text-xs">← THREADS</Link>
          <div className="silk mt-2">MODULE&nbsp;03&nbsp;/&nbsp;THREADS</div>
          {rctx?.loop && (
            <div className="mt-2 font-mono text-xs text-ink-3">
              <Link to={`/tasks/${rctx.loop.task_id}`} className="link-console">TASK&nbsp;#{rctx.loop.task_id}</Link>
              <span className="mx-1.5 text-ink-3/50">›</span>
              <Link to={`/tasks/${rctx.loop.task_id}#loop-${rctx.loop.id}`} className="link-console">LOOP&nbsp;#{rctx.loop.id}</Link>
              <span className="mx-1.5 text-ink-3/50">›</span>
              <span className="text-ink-2">THREAD</span>
              {rctx.task?.input?.trim() && (
                <span className="ml-2 inline-block max-w-[380px] truncate align-bottom text-ink-2"
                  title={rctx.task.input}>
                  ·&nbsp;{rctx.task.input.trim()}
                </span>
              )}
            </div>
          )}
          <h1 className="mt-1.5 max-w-[640px] truncate text-[21px] font-semibold tracking-tight"
            title={firstHuman?.content || ''}>
            {firstHuman?.content.trim() || 'Thread 详情'}
          </h1>
          <div className="mt-1.5 flex flex-wrap items-center gap-2 font-mono text-xs text-ink-3">
            <span title={tid}>Thread {tid?.slice(0, 13)}…</span>
            {t?.agent_name && <Link to={`/agents/${t.agent_name}`} className="link-console">Agent：{t.agent_name} →</Link>}
          </div>
        </div>
        {runMeta && <StatusPill color={runMeta.led} blink={runMeta.blink}>{runMeta.label}</StatusPill>}
      </div>

      {tid && <ReviewBar ctx={rctx} reload={() => { if (tid) api.reviewContext(tid).then(setRctx).catch(() => setRctx(null)) }} />}

      {err && <ErrorBanner title="Thread 加载失败" error={err} className="mb-5" />}

      {detail && (
        <Panel className="mb-5">
          <div className="grid grid-cols-3 gap-px">
            {[
              { label: 'RUNS', value: detail.runs.length },
              { label: '累计 TOKEN', value: detail.runs.reduce((a, r) => a + r.total_tokens, 0).toLocaleString() },
              { label: '最后活跃', value: fmtTime(t?.updated_at as string), full: fmtFullTime(t?.updated_at as string) },
            ].map((c, i) => (
              <div key={c.label} className={`px-4 pb-3 pt-3.5 ${i < 2 ? 'border-r border-hairline-soft' : ''}`} title={c.full}>
                <span className="font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-ink-3">{c.label}</span>
                <div className="mt-1 font-mono text-lg font-medium tabular-nums text-ink">{c.value}</div>
              </div>
            ))}
          </div>
        </Panel>
      )}

      {files.length > 0 && tid && (
        <Panel className="mb-5">
          <PanelHead>
            <span className="silk flex items-center gap-2">
              <Led color="green" />
              FILES&nbsp;/&nbsp;产物
            </span>
            <span className="silk">{files.length}&nbsp;FILES</span>
          </PanelHead>
          <PanelBody className="space-y-1.5">
            {files.map((p) => {
              const name = p.split('/').pop() || p
              return (
                <div key={p} className="flex items-center justify-between gap-3 rounded-[2px] border border-hairline-soft bg-inset px-3.5 py-2">
                  <span className="min-w-0 truncate font-mono text-xs text-ink" title={p}>
                    {name}
                    <span className="ml-2 text-ink-3">{p}</span>
                  </span>
                  <a href={api.artifactUrl(tid, p)} download={name}
                    onClick={(event) => {
                      event.preventDefault()
                      void api.downloadArtifact(tid, p).catch(() => window.alert('产物下载失败，请稍后重试'))
                    }}
                    className="link-console shrink-0 font-mono text-[11px]">下载 ↓</a>
                </div>
              )
            })}
          </PanelBody>
        </Panel>
      )}

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-5">
        {/* 消息与续跑 */}
        <Panel className="lg:col-span-3">
          <PanelHead>
            <span className="silk">MESSAGES</span>
            <span className="silk">5S&nbsp;REFRESH</span>
          </PanelHead>
          <PanelBody>
            <div className="mb-4 max-h-[52vh] space-y-2.5 overflow-y-auto pr-2">
              {messages.length === 0 && <div className="py-8 text-center text-sm text-ink-3">暂无消息</div>}
              {messages.map((m, i) => (
                <div key={i} className={`rounded-[2px] border px-4 py-2.5 text-sm leading-relaxed ${
                  m.role === 'human'
                    ? 'ml-8 border-signal/25 bg-signal/5 text-ink'
                    : m.role === 'ai'
                      ? 'mr-8 border-hairline-soft bg-raised/40 text-ink'
                      : 'mx-4 border-hairline-soft/60 bg-inset/50 text-ink-2'
                }`}>
                  <div className="silk mb-1">{m.role === 'human' ? '你' : m.role === 'ai' ? 'AGENT' : m.name || 'TOOL'}</div>
                  <CollapsibleContent text={m.content} tid={tid} files={files} kind={m.role === 'tool' ? 'tool' : 'message'} label={m.name} />
                </div>
              ))}
              {sending && (
                <div className="mr-8 flex items-center gap-2 rounded-[2px] border border-hairline-soft bg-inset px-4 py-2.5 text-sm text-ink-2">
                  <Led color="green" blink />Agent 正在处理…
                </div>
              )}
              <div ref={bottomRef} />
            </div>
            <div>
              <Textarea value={input} onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) && send()}
                rows={3} placeholder="继续向该 Agent 发送指令…（Ctrl/⌘+Enter 发送）"
                className={INPUT_CLS + ' min-h-[72px] resize-y'} disabled={sending} />
              <div className="mt-2.5 flex justify-end">
                <Button variant="signal" onClick={send} disabled={sending || !input.trim()}>{sending ? '执行中' : '发送'}</Button>
              </div>
            </div>
          </PanelBody>
        </Panel>

        {/* Run 历史 */}
        <Panel className="lg:col-span-2">
          <PanelHead>
            <span className="silk">RUNS</span>
            <span className="silk">{detail?.runs.length ?? 0}&nbsp;RUNS</span>
          </PanelHead>
          <PanelBody className="max-h-[64vh] space-y-2.5 overflow-y-auto">
            {detail?.runs.map((r) => {
              const rm = runStatusMeta(r.status)
              return (
                <InsetRow key={r.run_id}>
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-xs text-ink-3">{r.run_id.slice(0, 8)}</span>
                    <StatusPill color={rm.led} blink={rm.blink}>{rm.label}</StatusPill>
                  </div>
                  <div className="mt-2 grid grid-cols-3 gap-2 font-mono text-[11px] text-ink-3">
                    <div>Token <span className="tabular-nums text-ink">{r.total_tokens.toLocaleString()}</span></div>
                    <div>LLM 调用 <span className="tabular-nums text-ink">{r.llm_call_count}</span></div>
                    <div>消息 <span className="tabular-nums text-ink">{r.message_count}</span></div>
                  </div>
                  <div className="mt-2 font-mono text-[11px] text-ink-2">实际模型：{r.model_name || '系统默认（旧记录）'}</div>
                  <div className="mt-1 font-mono text-[11px] text-ink-3">{fmtTime(r.created_at)}</div>
                  {r.error && <ErrorBanner title="Run 执行失败" error={r.error} className="mt-2" />}
                </InsetRow>
              )
            })}
          </PanelBody>
        </Panel>
      </div>
    </div>
  )
}
