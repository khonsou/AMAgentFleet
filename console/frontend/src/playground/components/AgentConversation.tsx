import { useCallback, useEffect, useRef, useState } from 'react'
import type { AgentView } from '../core/types'
import { useOperations } from '@/protocol/useOperations'
import { normalizeOpsMessages } from '@/components/console/opsMessages'
import {
  FLEET_OPS_AGENT_ID,
  agentThreadStorageKey,
  readAgentThreadId,
  writeAgentThreadId,
} from '@/lib/fleetOps'

interface ChatMessage { id: string; role: 'human' | 'ai'; content: string; delivery?: 'sending' | 'failed' }
interface ThreadDetail { thread: { assistant_id?: string; agent_name?: string } }

function normalize(raw: unknown): ChatMessage[] {
  return normalizeOpsMessages(raw).flatMap((message, index) =>
    message.role === 'human' || message.role === 'ai'
      ? [{ id: `${index}-${message.role}`, role: message.role, content: message.content }]
      : [],
  )
}
function tempId() { return `optimistic-${Date.now()}-${Math.random().toString(16).slice(2)}` }
interface ThreadHandle { id: string; created: boolean }

export default function AgentConversation({ agent, onChanged }: { agent: AgentView; onChanged: () => void }) {
  const { execute } = useOperations()
  const storageKey = agentThreadStorageKey(agent.id)
  const threadPromise = useRef<Promise<ThreadHandle> | null>(null)
  const loadSequence = useRef(0)
  const [threadId, setThreadId] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [error, setError] = useState('')

  const loadMessages = useCallback(async (id: string) => {
    const sequence = ++loadSequence.current
    const response = await execute('threads.messages', { path_params: { tid: id } })
    if (sequence !== loadSequence.current) return
    const serverMessages = normalize(response.result)
    setMessages((current) => {
      const pending = current.filter((message) => message.delivery)
      return [...serverMessages, ...pending.filter((optimistic) =>
        !serverMessages.some((server) => server.role === optimistic.role && server.content === optimistic.content),
      )]
    })
    setError('')
  }, [execute])

  useEffect(() => {
    const saved = readAgentThreadId(agent.id)
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
  }, [agent.id, execute, loadMessages, storageKey])

  useEffect(() => {
    if (!threadId) return
    const timer = window.setInterval(() => {
      void loadMessages(threadId).catch((reason) => setError(String(reason)))
    }, 5000)
    return () => window.clearInterval(timer)
  }, [threadId, loadMessages])

  useEffect(() => {
    if (agent.id !== FLEET_OPS_AGENT_ID) return
    const syncThread = (event: StorageEvent) => {
      if (event.key !== storageKey || !event.newValue || event.newValue === threadId) return
      setThreadId(event.newValue)
      void loadMessages(event.newValue).catch((reason) => setError(String(reason)))
    }
    window.addEventListener('storage', syncThread)
    return () => window.removeEventListener('storage', syncThread)
  }, [agent.id, storageKey, threadId, loadMessages])

  const ensureThread = (firstMessage: string): Promise<ThreadHandle> => {
    if (threadId) return Promise.resolve({ id: threadId, created: false })
    const owner = !threadPromise.current
    if (owner) {
      threadPromise.current = execute('threads.create', {
        body: { assistant_id: agent.id, instruction: firstMessage, recursion_limit: 300 },
      }).then((response) => {
        const id = String((response.result as { thread_id: string }).thread_id)
        writeAgentThreadId(agent.id, id)
        setThreadId(id)
        return { id, created: true }
      }).catch((reason) => {
        threadPromise.current = null
        throw reason
      })
    }
    const pending = threadPromise.current
    return owner
      ? pending!
      : pending!.then((handle) => ({ ...handle, created: false }))
  }

  const send = () => {
    const content = input.trim()
    if (!content) return
    const optimistic: ChatMessage = { id: tempId(), role: 'human', content, delivery: 'sending' }
    setMessages((current) => [...current, optimistic])
    setInput('')
    setError('')
    void (async () => {
      try {
        const { id, created } = await ensureThread(content)
        if (!created) {
          await execute('threads.send', { path_params: { tid: id }, body: { content, recursion_limit: 300 } })
        }
        await loadMessages(id)
        onChanged()
      } catch (reason) {
        setMessages((current) => current.map((message) => message.id === optimistic.id ? { ...message, delivery: 'failed' } : message))
        setError(String(reason))
      }
    })()
  }

  return <section className="drawer-section agent-conversation">
    <h3>AGENT CHAT / {agent.id}</h3>
    <p className="drawer-subtitle">消息立即上屏，Agent 在后台回复；可以连续发送，无需等待上一条完成。</p>
    <div className="chat-timeline" aria-live="polite">
      {messages.map((message) => <article key={message.id} className={`chat-event ${message.role === 'human' ? 'human' : ''}`}><b>{message.role === 'human' ? `YOU${message.delivery === 'sending' ? ' · SENDING' : message.delivery === 'failed' ? ' · FAILED' : ''}` : agent.name}</b><div className="chat-event-content">{message.content}</div></article>)}
      {!messages.length && <div className="empty-state">给 {agent.name} 发送第一条消息开始对话。</div>}
    </div>
    <div className="chat-composer">
      <textarea aria-label={`给 ${agent.name} 的消息`} value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => {
        if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
          event.preventDefault()
          send()
        }
      }} placeholder={`发送给 ${agent.name}…`} />
      <div className="composer-hint">Ctrl / ⌘ + Enter 立即发送；Agent 后台执行，可继续输入下一条。</div>
      <button className="drawer-action" disabled={!input.trim()} onClick={send}>{threadId ? '发送' : '开始对话'}</button>
      {error && <p role="alert" className="operation-error">{error}</p>}
    </div>
  </section>
}
