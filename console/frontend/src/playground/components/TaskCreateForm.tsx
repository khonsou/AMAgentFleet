import { useState } from 'react'
import type { AgentView } from '../core/types'
import { useOperations } from '../protocol/useOperations'

export default function TaskCreateForm({ agents, onCreated }: { agents: AgentView[]; onCreated: (taskId: number) => void }) {
  const { execute, busy } = useOperations()
  const [input, setInput] = useState('')
  const [assignee, setAssignee] = useState('')
  const [error, setError] = useState('')

  const create = async () => {
    if (!input.trim()) return
    try {
      const response = await execute<{ task_id: number }>('tasks.create', {
        body: {
          input: input.trim(),
          ...(assignee ? { assignee } : {}),
          parent_task_id: null,
          model_profile: null,
          subagent_model_policy: 'auto',
        },
      })
      setInput('')
      setError('')
      onCreated(response.result.task_id)
    } catch (reason) { setError(String(reason)) }
  }

  const availableAgents = agents.filter((agent) => ['registered', 'running'].includes(agent.registryStatus))
  return <section className="drawer-section task-create-section">
    <h3>CREATE TASK</h3>
    <div className="operation-form">
      <label>Task 指令<textarea aria-label="新 Task 指令" value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => {
        if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
          event.preventDefault()
          void create()
        }
      }} placeholder="描述要完成的任务与交付要求…" /></label>
      <label>执行 Agent<select aria-label="新 Task 执行 Agent" value={assignee} onChange={(event) => setAssignee(event.target.value)}><option value="">暂不指派，进入 Task Pool</option>{availableAgents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label>
      <button className="drawer-action" disabled={busy || !input.trim()} onClick={() => void create()}>{busy ? '处理中…' : '检查并创建 Task'}</button>
      {error && <p role="alert" className="operation-error">{error}</p>}
    </div>
  </section>
}
