import { describe, expect, it } from 'vitest'
import { agentTasks, buildSceneViewModel, bubbleFor } from './model'
import type { FleetTask } from './types'

function task(overrides: Partial<FleetTask> = {}): FleetTask {
  return {
    id: 1, input: '测试', agent_name: 'unassigned', status: 'queued', pool_status: 'open',
    latest_loop_id: null, latest_loop_status: null, latest_thread_id: null,
    latest_deliverable: null, latest_message: null, claimants: [], result_preview: null,
    error: null, created_at: '2026-10-31T08:00:00Z', parent_task_id: null,
    ...overrides,
  }
}

describe('Playground ViewModel', () => {
  it('把竞争 LOOP 计入每个真实 claimant，而不是只算首选 Agent', () => {
    const current = task({ claimants: [
      { loop_id: 1, claimant_type: 'agent', claimant: 'scout', status: 'running', thread_id: 'a' },
      { loop_id: 2, claimant_type: 'agent', claimant: 'analyst', status: 'delivered', thread_id: 'b' },
    ] })
    expect(agentTasks(current, 'scout')).toBe(true)
    expect(agentTasks(current, 'analyst')).toBe(true)
    expect(agentTasks(current, 'writer')).toBe(false)
  })

  it('终态与归档实例不进入正在执行数量', () => {
    expect(agentTasks(task({ pool_status: 'done' }), 'scout')).toBe(false)
    expect(agentTasks(task({ claimants: [{ loop_id: 1, claimant_type: 'agent', claimant: 'scout', status: 'archived', thread_id: null }] }), 'scout')).toBe(false)
  })

  it('名字保持可见，气泡使用最新真实进展并限制长度', () => {
    const latest = task({ agent_name: 'scout', latest_message: `  最新${'进'.repeat(200)}展  `, updated_at: '2026-10-31T09:00:00Z' })
    const scene = buildSceneViewModel([{ name: 'scout', display_name: 'Scout', status: 'registered' }], [latest], 'now')
    expect(scene.agents[0].name).toBe('SCOUT')
    expect(scene.agents[0].latestTask).toBe(latest)
    expect(scene.agents[0].bubble?.length).toBeLessThanOrEqual(42)
    expect(scene.agents[0].bubble).toMatch(/…$/)
  })

  it('没有进展时使用明确状态，不伪造思考内容', () => {
    expect(bubbleFor(task({ pool_status: 'review' }))).toBe('OUTPUT 已交付，等待验收')
    expect(bubbleFor(task({ error: 'failed' }))).toBe('执行异常，需要处理')
  })
})
