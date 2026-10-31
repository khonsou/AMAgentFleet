import type { AgentView, FleetTask, RegistryAgent, SceneViewModel } from './types'

const ACTIVE_LOOPS = new Set(['claimed', 'running', 'delivered', 'returned'])

export function agentTasks(task: FleetTask, agentName: string): boolean {
  if (task.pool_status === 'done') return false
  if (task.claimants?.length) {
    return task.claimants.some((claimant) => claimant.claimant_type === 'agent' && claimant.claimant === agentName && ACTIVE_LOOPS.has(claimant.status))
  }
  return task.agent_name === agentName
}

function bubbleSnippet(value: string): string {
  const text = value.trim().replace(/\s+/g, ' ')
  return text.length > 42 ? `${text.slice(0, 41)}…` : text
}

export function bubbleFor(task: FleetTask | null): string | null {
  if (!task) return null
  if (task.error) return '执行异常，需要处理'
  if (task.latest_message?.trim()) return bubbleSnippet(task.latest_message)
  if (task.pool_status === 'review') return 'OUTPUT 已交付，等待验收'
  if (task.latest_deliverable?.trim()) return bubbleSnippet(task.latest_deliverable)
  if (task.latest_loop_status === 'returned') return '根据退回意见继续修改'
  if (task.latest_loop_status === 'running' || task.status === 'running') return '正在执行'
  if (task.agent_name === 'unassigned') return '等待领取'
  return '等待下一步'
}

function statusFor(agent: RegistryAgent, tasks: FleetTask[]): AgentView['status'] {
  if (!['registered', 'running'].includes(agent.status)) return 'offline'
  if (tasks.some((task) => task.error || task.status === 'error')) return 'error'
  if (tasks.some((task) => task.pool_status === 'review' || task.latest_loop_status === 'returned')) return 'waiting'
  if (tasks.some((task) => task.latest_loop_status === 'running' || task.status === 'running')) return 'working'
  return tasks.length ? 'waiting' : 'idle'
}

export function buildSceneViewModel(agents: RegistryAgent[], tasks: FleetTask[], generatedAt = new Date().toISOString()): SceneViewModel {
  const views = agents.filter((agent) => agent.status !== 'destroyed').map((agent) => {
    const owned = tasks.filter((task) => agentTasks(task, agent.name)).sort((a, b) => {
      const aTime = a.updated_at || a.created_at
      const bTime = b.updated_at || b.created_at
      return bTime.localeCompare(aTime) || b.id - a.id
    })
    const latestTask = owned[0] ?? null
    return {
      id: agent.name,
      name: (agent.display_name || agent.name).toUpperCase(),
      role: String(agent.identity?.role || agent.identity?.description || 'Agent'),
      registryStatus: agent.status,
      status: statusFor(agent, owned),
      tasks: owned,
      latestTask,
      bubble: bubbleFor(latestTask),
    }
  })
  return {
    agents: views,
    tasks,
    openTasks: tasks.filter((task) => task.pool_status !== 'done').length,
    activeAgents: views.filter((agent) => agent.status === 'working').length,
    generatedAt,
  }
}
