import { useEffect, useState } from 'react'
import type { AgentView } from '../core/types'
import { useOperations } from '@/protocol/useOperations'
import TaskConversation from './TaskConversation'

interface Props {
  agent: AgentView
  agents: AgentView[]
  onClose: () => void
  onChanged: () => void
}

/** 普通业务 Agent 没有独立私聊：对话始终选择或创建一个 Task。 */
export default function AgentTaskConversation({ agent, agents, onClose, onChanged }: Props) {
  const { execute, busy } = useOperations()
  const [taskId, setTaskId] = useState<number | null>(agent.latestTask?.id ?? null)
  const [input, setInput] = useState('')
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    setTaskId((current) => current && agent.tasks.some((task) => task.id === current)
      ? current
      : agent.latestTask?.id ?? null)
  }, [agent.id, agent.latestTask?.id, agent.tasks])

  const create = async () => {
    const content = input.trim()
    if (!content || creating) return
    setCreating(true)
    setError('')
    try {
      const response = await execute<{ task_id: number }>('tasks.create', {
        body: {
          input: content,
          assignee: agent.id,
          parent_task_id: null,
          model_profile: null,
          subagent_model_policy: 'auto',
        },
      })
      setInput('')
      setTaskId(response.result.task_id)
      onChanged()
    } catch (reason) {
      setError(String(reason))
    } finally {
      setCreating(false)
    }
  }

  if (taskId) {
    return <>
      {agent.tasks.length > 1 && <section className="drawer-section">
        <label>当前 Task
          <select aria-label="当前 Task" value={taskId} onChange={(event) => setTaskId(Number(event.target.value))}>
            {agent.tasks.map((task) => <option key={task.id} value={task.id}>
              #{task.id} · {task.input.slice(0, 80)}
            </option>)}
          </select>
        </label>
        <p className="drawer-subtitle">与 Task Detail 使用同一个执行 Thread。</p>
      </section>}
      <TaskConversation key={taskId} taskId={taskId} agents={agents} onClose={onClose} onChanged={onChanged} embedded />
    </>
  }

  const available = ['registered', 'running'].includes(agent.registryStatus)
  return <section className="drawer-section agent-task-create">
    <h3>CREATE TASK / {agent.id}</h3>
    <p className="drawer-subtitle">当前没有可继续的 Task。首条消息会创建并指派 Task，随后进入同一个 Task 对话。</p>
    <div className="chat-composer">
      <textarea aria-label={`给 ${agent.name} 的新 Task 指令`} value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => {
        if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
          event.preventDefault()
          void create()
        }
      }} placeholder={`描述要交给 ${agent.name} 完成的 Task…`} disabled={!available || creating} />
      <div className="composer-hint">Ctrl / ⌘ + Enter 创建；不会另建 Agent 私聊 Thread。</div>
      <button className="drawer-action" disabled={!available || creating || busy || !input.trim()} onClick={() => void create()}>
        {creating ? '创建中…' : '创建 Task 并开始对话'}
      </button>
      {!available && <p className="drawer-subtitle">Agent 当前未运行，启动后才能创建 Task。</p>}
      {error && <p role="alert" className="operation-error">{error}</p>}
    </div>
  </section>
}
