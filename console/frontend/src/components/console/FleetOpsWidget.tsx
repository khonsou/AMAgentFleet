import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { ExternalLink, MessageSquare, X } from 'lucide-react'
import { api, type ChatMessage } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Led } from '@/components/console/Status'
import { normalizeOpsMessages } from './opsMessages'

const STORAGE_KEY = 'fleet-ops-thread-id'
const AGENT_ID = 'fleet-ops-01'
const INPUT_CLS = 'rounded-[2px] border-hairline bg-inset text-ink placeholder:text-ink-3 focus-visible:ring-signal/40'

/** 首页自然语言操作入口。业务动作统一由已注册的 Fleet Ops Agent 与协议执行。 */
export default function FleetOpsWidget() {
  const [open, setOpen] = useState(false)
  const [threadId, setThreadId] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const creating = useRef(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  const loadMessages = async (id: string) => {
    const raw = await api.messages(id)
    const normalized = normalizeOpsMessages(raw)
    setMessages(normalized)
    return normalized
  }

  useEffect(() => {
    if (!open || threadId || creating.current) return
    let active = true
    creating.current = true
    setLoading(true)
    setError('')
    const initialize = async () => {
      const saved = window.localStorage.getItem(STORAGE_KEY)
      try {
        if (saved) {
          const detail = await api.thread(saved)
          if (detail.thread.assistant_id === AGENT_ID) {
            await loadMessages(saved)
            if (active) setThreadId(saved)
            return
          }
          // 旧版本可能留下 assistant_id=null / lead_agent 的浮窗会话，不能继续恢复。
          window.localStorage.removeItem(STORAGE_KEY)
        }
        const created = await api.create(undefined, AGENT_ID)
        if (!created.thread_id) throw new Error('操作助手未返回 Thread ID')
        window.localStorage.setItem(STORAGE_KEY, created.thread_id)
        if (active) {
          setThreadId(created.thread_id)
          setMessages([])
        }
      } catch (e) {
        if (!active) return
        // 持久会话被清理后允许自动重建；网络与服务器错误保留原 ID 便于重试。
        if (saved && String(e).startsWith('Error: 404:')) {
          window.localStorage.removeItem(STORAGE_KEY)
          creating.current = false
        }
        setError(String(e))
      } finally {
        if (active) setLoading(false)
        creating.current = false
      }
    }
    void initialize()
    return () => { active = false }
  }, [open, threadId, attempt])

  useEffect(() => {
    if (open) bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length, sending, open])

  const retry = () => {
    setThreadId('')
    setMessages([])
    setError('')
    setAttempt((value) => value + 1)
  }

  const send = async () => {
    const content = input.trim()
    if (!content || !threadId || sending || loading) return
    setSending(true)
    setError('')
    try {
      await api.send(threadId, content, AGENT_ID)
      setInput('')
      const refreshed = await loadMessages(threadId)
      if (!refreshed.length || refreshed[refreshed.length - 1].role !== 'ai') {
        setError('Agent 已结束，但没有返回可展示的结果；请打开完整会话检查运行状态。')
      }
    } catch (e) {
      setError(String(e))
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col items-end gap-2.5 sm:bottom-6 sm:right-6">
      {open && (
        <section aria-label="舰队操作助手" className="flex h-[min(680px,calc(100dvh-96px))] w-[min(440px,calc(100vw-32px))] flex-col overflow-hidden rounded-[3px] border border-hairline bg-panel shadow-2xl shadow-black/40">
          <header className="flex items-center gap-2.5 border-b border-hairline bg-raised px-4 py-3">
            <Led color="green" />
            <div className="min-w-0">
              <h2 className="text-sm font-medium text-ink">舰队操作助手</h2>
              <div className="silk mt-0.5">FLEET OPS / {AGENT_ID}</div>
            </div>
            {threadId && (
              <Link to={`/threads/${threadId}`} className="link-console ml-auto shrink-0 text-ink-3" title="打开完整会话" aria-label="打开完整会话">
                <ExternalLink size={15} />
              </Link>
            )}
            <button type="button" onClick={() => setOpen(false)} className="shrink-0 p-1 text-ink-3 hover:text-ink" aria-label="关闭操作助手">
              <X size={17} />
            </button>
          </header>

          <div className="border-b border-hairline-soft px-4 py-2.5 text-[11px] leading-relaxed text-ink-3">
            用自然语言查询或操作舰队。普通对话不会创建任务；写操作会先说明动作和参数，待你明确确认后执行。
          </div>

          <div className="min-h-0 flex-1 space-y-2.5 overflow-y-auto px-4 py-3" aria-live="polite">
            {loading && <div className="py-8 text-center font-mono text-xs text-ink-3">正在连接操作助手…</div>}
            {!loading && messages.length === 0 && !error && (
              <div className="rounded-[2px] border border-hairline-soft bg-inset/60 px-3.5 py-3 text-xs leading-relaxed text-ink-2">
                可以问“当前有哪些待验收任务”，也可以说“创建一个任务”。涉及变更时，助手会先向你确认。
              </div>
            )}
            {messages.map((message, index) => (
              <div key={`${message.role}-${index}`} className={`rounded-[2px] border px-3 py-2 text-[13px] leading-relaxed ${
                message.role === 'human' ? 'ml-7 border-signal/25 bg-signal/5 text-ink' : 'mr-4 border-hairline-soft bg-inset/60 text-ink'
              }`}>
                <div className="silk mb-1">{message.role === 'human' ? '你' : 'AGENT'}</div>
                <div className="max-h-56 overflow-y-auto whitespace-pre-wrap break-words">{message.content}</div>
              </div>
            ))}
            {sending && (
              <div className="mr-4 flex items-center gap-2 rounded-[2px] border border-hairline-soft bg-inset px-3 py-2 text-xs text-ink-2">
                <Led color="green" blink />助手正在执行，完成后会返回结果…
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {error && (
            <div role="alert" className="mx-4 mb-2 rounded-[2px] border border-led-red/30 bg-led-red/10 px-3 py-2 text-xs leading-relaxed text-led-red">
              <div className="break-words">{error}</div>
              {!threadId && <button type="button" className="link-console mt-1 text-xs" onClick={retry}>重新连接</button>}
            </div>
          )}

          <footer className="border-t border-hairline p-3">
            <Textarea aria-label="给舰队操作助手的指令" value={input} onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                  e.preventDefault()
                  void send()
                }
              }}
              rows={3} placeholder="查询或操作舰队，Ctrl/⌘+Enter 发送…"
              className={INPUT_CLS + ' min-h-[68px] resize-none text-[13px]'} disabled={sending || loading || !threadId} />
            <div className="mt-2 flex items-center gap-2">
              <span className="min-w-0 truncate font-mono text-[10px] text-ink-3" title={threadId}>
                {threadId ? `THREAD ${threadId}` : '持久会话'}
              </span>
              <Button variant="signal" size="sm" className="ml-auto shrink-0" onClick={() => void send()} disabled={sending || loading || !threadId || !input.trim()}>
                {sending ? '执行中…' : '发送'}
              </Button>
            </div>
          </footer>
        </section>
      )}
      {!open && (
        <Button variant="signal" size="lg" onClick={() => setOpen(true)} className="gap-2 shadow-lg shadow-black/25" aria-label="打开舰队操作助手">
          <MessageSquare size={18} />舰队操作助手
        </Button>
      )}
    </div>
  )
}
