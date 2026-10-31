import { useEffect, useState } from 'react'
import type { AgentView } from '../core/types'
import { useOperations } from '../protocol/useOperations'

interface ChatMessage { id: string; role: 'human' | 'ai'; content: string }
interface ThreadDetail { thread: { assistant_id?: string; agent_name?: string } }

function textOf(value: unknown): string {
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return value.map((part) => typeof part === 'string' ? part : textOf(part)).join('')
  if (value && typeof value === 'object' && 'text' in value) return textOf((value as { text: unknown }).text)
  return ''
}
function normalize(raw: unknown): ChatMessage[] {
  const rows = Array.isArray(raw) ? raw : (raw as { messages?: unknown[] })?.messages || []
  return rows.flatMap((row, index) => {
    const event = row as Record<string, unknown>
    const inner = (event.content && typeof event.content === 'object' ? event.content : event) as Record<string, unknown>
    const type = String(inner.type || event.event_type || '')
    const role = ['human', 'user', 'llm.human.input'].includes(type) ? 'human' : ['ai', 'assistant', 'llm.ai.response'].includes(type) ? 'ai' : null
    const content = textOf(inner.content)
    return role && content.trim() ? [{ id: `${index}-${type}`, role, content }] : []
  })
}

export default function AgentConversation({ agent, onChanged }: { agent: AgentView; onChanged: () => void }) {
  const { execute, busy } = useOperations()
  const storageKey = `playground-agent-thread-${agent.id}`
  const [threadId, setThreadId] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [error, setError] = useState('')
  const [sending, setSending] = useState(false)

  const loadMessages = async (id: string) => {
    const response = await execute('threads.messages', { path_params: { tid: id } })
    setMessages(normalize(response.result))
  }

  useEffect(() => {
    const saved = window.localStorage.getItem(storageKey)
    if (!saved) return
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const detail = await execute<ThreadDetail>('threads.get', { path_params: { tid: saved } })
          const owner = detail.result.thread.assistant_id || detail.result.thread.agent_name
          if (owner !== agent.id) {
            window.localStorage.removeItem(storageKey)
            return
          }
          setThreadId(saved)
          await loadMessages(saved)
        } catch {
          window.localStorage.removeItem(storageKey)
        }
      })()
    }, 0)
    return () => window.clearTimeout(timer)
    // Agent 切换时由父组件 key 触发完整重挂载。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent.id])

  const send = async () => {
    const content = input.trim()
    if (!content || sending || busy) return
    setSending(true)
    setError('')
    try {
      let id = threadId
      if (!id) {
        const response = await execute<{ thread_id: string }>('threads.create', {
          body: { assistant_id: agent.id, instruction: content, recursion_limit: 300 },
        })
        id = response.result.thread_id
        window.localStorage.setItem(storageKey, id)
        setThreadId(id)
      } else {
        await execute('threads.send', { path_params: { tid: id }, body: { content, recursion_limit: 300 } })
      }
      setInput('')
      await loadMessages(id)
      onChanged()
    } catch (reason) { setError(String(reason)) } finally { setSending(false) }
  }

  return <section className="drawer-section agent-conversation">
    <h3>AGENT CHAT / {agent.id}</h3>
    <p className="drawer-subtitle">直接对话不会自动创建业务 Task；首次发送会建立持久 Thread，后续在同一 Thread 中继续。</p>
    <div className="chat-timeline">
      {messages.map((message) => <article key={message.id} className={`chat-event ${message.role === 'human' ? 'human' : ''}`}><b>{message.role === 'human' ? 'YOU' : agent.name}</b><div className="chat-event-content">{message.content}</div></article>)}
      {!messages.length && <div className="empty-state">给 {agent.name} 发送第一条消息开始对话。</div>}
    </div>
    <div className="chat-composer">
      <textarea aria-label={`给 ${agent.name} 的消息`} value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => {
        if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
          event.preventDefault()
          void send()
        }
      }} placeholder={`发送给 ${agent.name}…`} />
      <div className="composer-hint">Ctrl / ⌘ + Enter 发送；写操作会先展示确认窗口。</div>
      <button className="drawer-action" disabled={sending || busy || !input.trim()} onClick={() => void send()}>{sending || busy ? '等待确认或 Agent 执行…' : threadId ? '确认并发送' : '确认并开始对话'}</button>
      {error && <p role="alert" className="operation-error">{error}</p>}
    </div>
  </section>
}
