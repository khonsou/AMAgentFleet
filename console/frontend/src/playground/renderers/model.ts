import { bubbleFor } from '../core/model'
import type { AgentVisualStatus, FleetTask, SceneViewModel } from '../core/types'

const POSITIONS = [
  [-2.2, 0, 1.6], [-4.3, 0, 0.3], [1.7, 0, 2.7],
  [-3.7, 0, 3.1], [4.3, 0, -0.2], [3.0, 0, -2.0],
]
const COLORS = ['#ef5b2a', '#e7ad4a', '#7a9c88', '#d9846f', '#ab6b62', '#7398aa', '#9b8ec4', '#82a975']

export function positionForAgent(index: number): number[] {
  if (POSITIONS[index]) return POSITIONS[index]
  const extra = index - POSITIONS.length
  return [-5.4 + (extra % 4) * 3.4, 0, 4.0]
}

function visualTaskStatus(task: FleetTask): string {
  if (task.error || task.status === 'error') return 'error'
  if (task.pool_status === 'done') return 'done'
  if (task.pool_status === 'review') return 'delivered'
  if (task.latest_loop_status === 'returned') return 'waiting'
  if (task.latest_loop_status === 'running' || task.status === 'running') return 'working'
  if (task.agent_name === 'unassigned') return 'queued'
  return 'waiting'
}

export function toDioramaSnapshot(model: SceneViewModel) {
  const ownedTasks = model.agents.flatMap((agent) => agent.tasks.map((task) => ({
    id: `T-${task.id}`,
    taskId: task.id,
    title: task.input,
    short: bubbleFor(task) || '等待下一步',
    agent: agent.id,
    project: null,
    status: visualTaskStatus(task),
    updated: Date.parse(task.updated_at || task.created_at) || task.id,
  })))
  const ownedIds = new Set(ownedTasks.map((task) => task.taskId))
  const poolOnlyTasks = model.tasks.filter((task) => !ownedIds.has(task.id)).map((task) => ({
    id: `T-${task.id}`,
    taskId: task.id,
    title: task.input,
    short: bubbleFor(task) || '等待下一步',
    agent: null,
    project: null,
    status: visualTaskStatus(task),
    updated: Date.parse(task.updated_at || task.created_at) || task.id,
  }))
  return {
    projects: [],
    agents: model.agents.map((agent, index) => ({
      id: agent.id,
      name: agent.name,
      role: agent.id.includes('cmd') ? 'coordinator' : agent.id.includes('analyst') ? 'data' : agent.role,
      color: COLORS[index % COLORS.length],
      status: agent.status satisfies AgentVisualStatus,
      project: null,
      position: positionForAgent(index),
    })),
    tasks: [...ownedTasks, ...poolOnlyTasks],
  }
}
