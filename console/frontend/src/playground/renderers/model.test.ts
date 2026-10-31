import { describe, expect, it } from 'vitest'
import { positionForAgent, toDioramaSnapshot } from './model'
import type { FleetTask } from '../core/types'

describe('Diorama Agent 排布', () => {
  it('新增 Agent 不会被排到草地和默认视野之外', () => {
    for (let index = 0; index < 30; index += 1) {
      const [x, y, z] = positionForAgent(index)
      expect(Math.abs(x)).toBeLessThanOrEqual(6.5)
      expect(y).toBe(0)
      expect(Math.abs(z)).toBeLessThanOrEqual(4.5)
    }
  })

  it('未分配 Task 也会进入 Task Pool 大屏刷新数据', () => {
    const unassigned: FleetTask = {
      id: 99, input: '未分配新任务', agent_name: 'unassigned', status: 'queued', pool_status: 'open',
      latest_loop_id: null, latest_loop_status: null, latest_thread_id: null, latest_deliverable: null,
      latest_message: null, claimants: [], result_preview: null, error: null,
      created_at: '2026-10-31T08:00:00Z', parent_task_id: null,
    }
    const snapshot = toDioramaSnapshot({ agents: [], tasks: [unassigned], openTasks: 1, activeAgents: 0, generatedAt: 'now' })
    expect(snapshot.tasks).toHaveLength(1)
    expect(snapshot.tasks[0]).toMatchObject({ id: 'T-99', taskId: 99, agent: null, status: 'queued' })
  })
})
