export const FLEET_OPS_AGENT_ID = 'fleet-ops-01'
export const FLEET_OPS_THREAD_STORAGE_KEY = 'fleet-ops-thread-id'

const LEGACY_PLAYGROUND_KEY = `playground-agent-thread-${FLEET_OPS_AGENT_ID}`

export function agentThreadStorageKey(agentId: string): string {
  return agentId === FLEET_OPS_AGENT_ID
    ? FLEET_OPS_THREAD_STORAGE_KEY
    : `playground-agent-thread-${agentId}`
}

export function readAgentThreadId(agentId: string): string {
  const key = agentThreadStorageKey(agentId)
  const saved = window.localStorage.getItem(key)
  if (saved || agentId !== FLEET_OPS_AGENT_ID) return saved || ''

  // 只迁移 Playground 旧键；两个入口已有不同会话时以全局 Ops 会话为准，
  // 不拼接或改写历史 Thread。
  const legacy = window.localStorage.getItem(LEGACY_PLAYGROUND_KEY)
  if (legacy) {
    window.localStorage.setItem(key, legacy)
    window.localStorage.removeItem(LEGACY_PLAYGROUND_KEY)
  }
  return legacy || ''
}

export function writeAgentThreadId(agentId: string, threadId: string): void {
  window.localStorage.setItem(agentThreadStorageKey(agentId), threadId)
  if (agentId === FLEET_OPS_AGENT_ID) window.localStorage.removeItem(LEGACY_PLAYGROUND_KEY)
}
