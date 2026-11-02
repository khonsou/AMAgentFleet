import { beforeEach, describe, expect, it } from 'vitest'
import {
  FLEET_OPS_AGENT_ID,
  FLEET_OPS_THREAD_STORAGE_KEY,
  agentThreadStorageKey,
  readAgentThreadId,
  writeAgentThreadId,
} from './fleetOps'

describe('Fleet Ops 共享会话键', () => {
  beforeEach(() => window.localStorage.clear())

  it('Playground 与全局浮窗使用同一个 key', () => {
    expect(agentThreadStorageKey(FLEET_OPS_AGENT_ID)).toBe(FLEET_OPS_THREAD_STORAGE_KEY)
    writeAgentThreadId(FLEET_OPS_AGENT_ID, 'shared-ops-thread')
    expect(window.localStorage.getItem(FLEET_OPS_THREAD_STORAGE_KEY)).toBe('shared-ops-thread')
    expect(readAgentThreadId(FLEET_OPS_AGENT_ID)).toBe('shared-ops-thread')
  })

  it('只在全局键缺失时迁移旧 Playground 会话', () => {
    window.localStorage.setItem('playground-agent-thread-fleet-ops-01', 'legacy-ops-thread')
    expect(readAgentThreadId(FLEET_OPS_AGENT_ID)).toBe('legacy-ops-thread')
    expect(window.localStorage.getItem(FLEET_OPS_THREAD_STORAGE_KEY)).toBe('legacy-ops-thread')
    expect(window.localStorage.getItem('playground-agent-thread-fleet-ops-01')).toBeNull()
  })
})
