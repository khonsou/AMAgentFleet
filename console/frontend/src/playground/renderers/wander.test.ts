import { describe, expect, it } from 'vitest'
import { createWanderState, updateWander, WANDER_SPEED, type WanderState } from './wander'

const context = {
  enabled: true,
  mayStart: true,
  position: { x: 0, z: 0 },
  home: { x: 0, z: 0 },
  others: [],
  elapsed: 2,
  delta: 0.1,
  random: () => 0,
}

function walkingState(): WanderState {
  return { phase: 'walk', target: { x: 1, z: 0 }, nextDecisionAt: 0, walkPhase: 0 }
}

describe('闲置 Agent 随机游走', () => {
  it('休息计时结束且允许出发时，以限定速度走向目标', () => {
    const state = createWanderState(0, () => 0)
    const result = updateWander(state, context)
    expect(state.phase).toBe('walk')
    expect(result.moving).toBe(true)
    expect(result.position.x).toBeCloseTo(WANDER_SPEED * 0.1)
    expect(result.position.z).toBeCloseTo(0)
    expect(result.heading).toBeCloseTo(Math.PI / 2)
  })

  it('未到决策时间或同时行走数量已满时保持休息', () => {
    const early = createWanderState(0, () => 0)
    expect(updateWander(early, { ...context, elapsed: 0 }).moving).toBe(false)
    const waiting = createWanderState(0, () => 0)
    expect(updateWander(waiting, { ...context, mayStart: false }).moving).toBe(false)
    expect(waiting.phase).toBe('rest')
  })

  it('被禁用时会取消游走，供任务事件和选中聚焦接管', () => {
    const state = walkingState()
    const result = updateWander(state, { ...context, enabled: false })
    expect(result.moving).toBe(false)
    expect(result.position).toEqual(context.position)
    expect(state.phase).toBe('rest')
    expect(state.target).toBeNull()
  })

  it('行走路径会与其他 Agent 过近时停下并稍后重试', () => {
    const state = walkingState()
    const result = updateWander(state, { ...context, others: [{ x: 0.3, z: 0 }] })
    expect(result.moving).toBe(false)
    expect(result.position).toEqual(context.position)
    expect(state.phase).toBe('rest')
    expect(state.nextDecisionAt).toBeGreaterThan(context.elapsed)
  })

  it('初始站位重叠时允许彼此远离，不会被避让规则锁死', () => {
    const state = walkingState()
    const result = updateWander(state, { ...context, others: [{ x: 0, z: 0 }] })
    expect(result.moving).toBe(true)
    expect(result.position.x).toBeGreaterThan(0)
  })

  it('抵达目标后进入 2–6 秒休息', () => {
    const state = walkingState()
    state.target = { x: 0.05, z: 0 }
    const result = updateWander(state, context)
    expect(result.moving).toBe(false)
    expect(state.phase).toBe('rest')
    expect(state.nextDecisionAt).toBeGreaterThanOrEqual(4)
    expect(state.nextDecisionAt).toBeLessThanOrEqual(8)
  })

  it('随机目标始终留在草地活动范围内', () => {
    const state = createWanderState(0, () => 0.99)
    state.nextDecisionAt = 0
    updateWander(state, { ...context, home: { x: -5.4, z: 4 }, position: { x: -5.4, z: 4 } })
    expect(state.target).not.toBeNull()
    expect(Math.abs(state.target!.x)).toBeLessThanOrEqual(6.25)
    expect(state.target!.z).toBeGreaterThanOrEqual(-2.5)
    expect(state.target!.z).toBeLessThanOrEqual(4.2)
  })
})
