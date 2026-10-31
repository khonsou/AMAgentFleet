import { useState } from 'react'
import type { AgentView } from '../core/types'
import { useOperations } from '../protocol/useOperations'

export default function TaskClaimPanel({ agents, onClaim }: { agents: AgentView[]; onClaim: (agentId: string) => Promise<void> }) {
  const { busy } = useOperations()
  const available = agents.filter((agent) => ['registered', 'running'].includes(agent.registryStatus) && agent.id !== 'fleet-ops-01')
  const [assignee, setAssignee] = useState('')
  const [error, setError] = useState('')
  const selected = assignee || available[0]?.id || ''

  const claim = async () => {
    if (!selected) return
    try {
      await onClaim(selected)
      setError('')
    } catch (reason) { setError(String(reason)) }
  }

  return <section className="claim-panel">
    <div className="drawer-kicker">ASSIGN AGENT / START EXECUTION</div>
    <p className="drawer-subtitle">这个 Task 尚未被领取。选择 Agent 后将创建执行实例并开始工作，OUTPUT 会自动出现在当前群聊。</p>
    {available.length ? <div className="claim-controls">
      <select aria-label="领取 Task 的 Agent" value={selected} onChange={(event) => setAssignee(event.target.value)}>{available.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select>
      <button className="drawer-action" disabled={busy || !selected} onClick={() => void claim()}>{busy ? '处理中…' : '检查并指派 Agent'}</button>
    </div> : <div className="empty-state">当前没有可领取任务的 Agent。</div>}
    {error && <p role="alert" className="operation-error">{error}</p>}
  </section>
}
