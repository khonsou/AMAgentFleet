import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { AgentView, TaskConversation as Conversation } from '../core/types'
import { useOperations } from '../protocol/useOperations'
import TaskClaimPanel from './TaskClaimPanel'

interface Props {
  taskId: number
  agents: AgentView[]
  onClose: () => void
  onChanged: () => void
}

export default function TaskConversation({ taskId, agents, onClose, onChanged }: Props) {
  const { execute, busy } = useOperations()
  const [conversation, setConversation] = useState<Conversation | null>(null)
  const [error, setError] = useState('')
  const [recipient, setRecipient] = useState('group')
  const [input, setInput] = useState('')
  const [returnTarget, setReturnTarget] = useState<number | null>(null)
  const [returnComment, setReturnComment] = useState('')
  const recipientInitialized = useRef(false)

  const load = useCallback(async () => {
    try {
      const response = await execute<Conversation>('tasks.conversation', { path_params: { task_id: taskId } })
      const next = response.result
      setConversation(next)
      if (!recipientInitialized.current) {
        const preferred = next.participants.find((item) => item.status === 'running' && item.thread_id) || next.participants.find((item) => item.thread_id)
        setRecipient(preferred ? String(preferred.loop_id) : 'group')
        recipientInitialized.current = true
      }
      setError('')
    } catch (reason) { setError(String(reason)) }
  }, [execute, taskId])

  useEffect(() => {
    recipientInitialized.current = false
    const initial = window.setTimeout(() => void load(), 0)
    const timer = window.setInterval(() => void load(), 5000)
    return () => {
      window.clearTimeout(initial)
      window.clearInterval(timer)
    }
  }, [load])

  const timeline = useMemo(() => {
    if (!conversation) return []
    const messages = conversation.messages.map((item) => ({ ...item, timelineKind: item.kind }))
    const outputs = conversation.outputs.map((item) => ({ ...item, kind: 'output', timelineKind: 'output' }))
    return [...messages, ...outputs].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id))
  }, [conversation])

  const send = async () => {
    const content = input.trim()
    if (!content || !conversation) return
    if (recipient === 'group') {
      await execute('tasks.comment', { path_params: { task_id: taskId }, body: { content, loop_id: null } })
    } else {
      const participant = conversation.participants.find((item) => String(item.loop_id) === recipient)
      if (!participant?.thread_id) throw new Error('当前执行者尚未启动 Thread，请改用全组评论。')
      await execute('threads.send', { path_params: { tid: participant.thread_id }, body: { content, recursion_limit: 300 } })
    }
    setInput('')
    await load()
    onChanged()
  }

  const claim = async (agentId: string) => {
    await execute('loops.claim', { path_params: { task_id: taskId }, body: { claimant_type: 'agent', claimant: agentId, model_profile: null } })
    await load()
    onChanged()
  }

  const acceptOutput = async (loopId: number) => {
    await execute('tasks.accept', { path_params: { task_id: taskId }, body: { loop_id: loopId } })
    await load()
    onChanged()
  }

  const returnOutput = async (loopId: number) => {
    if (!returnComment.trim()) return
    await execute('loops.return', { path_params: { loop_id: loopId }, body: { comment: returnComment.trim() } })
    setReturnTarget(null)
    setReturnComment('')
    await load()
    onChanged()
  }

  return <>
    <div className="drawer-top"><span className="drawer-kicker">TASK GROUP CHAT / #{taskId}</span><button className="drawer-close" onClick={onClose} aria-label="关闭群聊">×</button></div>
    <h2>{conversation?.task.input || `Task #${taskId}`}</h2>
    <p className="drawer-subtitle">所有 Agent、subagent 与人都在同一个 Task 群聊中协作；技术执行细节保留在消息来源中。</p>
    {error && <p role="alert" className="operation-error">{error}</p>}
    {!conversation && !error && <p className="drawer-subtitle">正在聚合 Task 对话…</p>}
    {conversation && <>
      <div className="task-flow">CREATE → ASSIGN → OUTPUT → REVIEW</div>
      {!conversation.participants.length && conversation.task.pool_status !== 'done' && <TaskClaimPanel agents={agents} onClaim={claim} />}
      <section className="drawer-section"><h3>PARTICIPANTS / {conversation.participants.length}</h3><div className="participant-list">
        {conversation.participants.map((participant) => <button key={participant.id} className="drawer-task" onClick={() => setRecipient(String(participant.loop_id))}><span><b>{participant.name}</b><small>{participant.type} · {participant.status}</small></span><span>{String(recipient) === String(participant.loop_id) ? '●' : '○'}</span></button>)}
      </div></section>
      <section className="chat-timeline" aria-live="polite">
        {timeline.map((item) => <article key={item.id} className={`chat-event ${item.timelineKind === 'output' ? 'output' : item.timelineKind === 'subagent' ? 'subagent' : item.actor === 'human' ? 'human' : ''}`}>
          <b>{item.timelineKind === 'output' ? `OUTPUT · ${item.actor} · ${item.status}` : item.actor}</b>
          <div className="chat-event-content">{item.content}</div>
          {item.timelineKind === 'output' && conversation.task.pool_status === 'review' && item.status === 'delivered' && <>
            <div className="output-actions"><button onClick={() => void acceptOutput(item.loop_id)} disabled={busy}>采纳</button><button onClick={() => setReturnTarget(item.loop_id)} disabled={busy}>退回</button></div>
            {returnTarget === item.loop_id && <div className="return-form"><textarea aria-label="退回意见" value={returnComment} onChange={(event) => setReturnComment(event.target.value)} placeholder="说明需要修改的内容，将在原 Thread 中继续…" /><button className="drawer-action" disabled={busy || !returnComment.trim()} onClick={() => void returnOutput(item.loop_id)}>检查并确认退回</button></div>}
          </>}
        </article>)}
        {!timeline.length && <div className="empty-state">还没有消息；可以从下方开始。</div>}
      </section>
      <div className="chat-composer">
        <div className="chat-recipients"><select aria-label="群聊收件人" value={recipient} onChange={(event) => setRecipient(event.target.value)}><option value="group">全组（只写评论，不启动 RUN）</option>{conversation.participants.filter((item) => item.thread_id).map((item) => <option key={item.id} value={item.loop_id}>{item.name}（继续其 Thread）</option>)}</select></div>
        <textarea aria-label="群聊消息" value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => {
          if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
            event.preventDefault()
            void send().catch((reason) => setError(String(reason)))
          }
        }} placeholder="输入指导、评论或交付要求…" />
        <div className="composer-hint">Ctrl / ⌘ + Enter 发送；写操作会先展示确认窗口。</div>
        <button className="drawer-action" onClick={() => void send().catch((reason) => setError(String(reason)))} disabled={busy || !input.trim()}>{busy ? '执行中…' : '确认并发送'}</button>
      </div>
    </>}
  </>
}
