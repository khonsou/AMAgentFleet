export interface WanderPoint {
  x: number
  z: number
}

export interface WanderState {
  phase: 'rest' | 'walk'
  target: WanderPoint | null
  nextDecisionAt: number
  walkPhase: number
}

interface WanderContext {
  enabled: boolean
  mayStart: boolean
  position: WanderPoint
  home: WanderPoint
  others: WanderPoint[]
  elapsed: number
  delta: number
  random?: () => number
}

export const WANDER_SPEED = 0.46
export const WANDER_HOME_RADIUS = 1.35
const BOUNDS = { minX: -6.25, maxX: 6.25, minZ: -2.5, maxZ: 4.2 }
const ARRIVAL_DISTANCE = 0.08
const TARGET_CLEARANCE = 1.08
const WALK_CLEARANCE = 0.92

const distance = (a: WanderPoint, b: WanderPoint) => Math.hypot(a.x - b.x, a.z - b.z)
const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value))

function rest(state: WanderState, elapsed: number, random: () => number, delay = 2) {
  state.phase = 'rest'
  state.target = null
  state.nextDecisionAt = elapsed + delay + random() * (delay === 2 ? 4 : 1.2)
}

function clearOfOthers(point: WanderPoint, others: WanderPoint[], clearance: number) {
  return others.every((other) => distance(point, other) >= clearance)
}

function chooseTarget(position: WanderPoint, home: WanderPoint, others: WanderPoint[], random: () => number) {
  for (let attempt = 0; attempt < 12; attempt += 1) {
    const angle = random() * Math.PI * 2
    const radius = 0.4 + random() * (WANDER_HOME_RADIUS - 0.4)
    const candidate = {
      x: clamp(home.x + Math.cos(angle) * radius, BOUNDS.minX, BOUNDS.maxX),
      z: clamp(home.z + Math.sin(angle) * radius, BOUNDS.minZ, BOUNDS.maxZ),
    }
    if (distance(candidate, position) >= 0.4 && clearOfOthers(candidate, others, TARGET_CLEARANCE)) return candidate
  }
  return null
}

export function createWanderState(index: number, random: () => number = Math.random): WanderState {
  return {
    phase: 'rest',
    target: null,
    nextDecisionAt: 1.2 + index * 0.55 + random() * 2.5,
    walkPhase: index * 1.37,
  }
}

export function updateWander(state: WanderState, context: WanderContext) {
  const { enabled, mayStart, position, home, others, elapsed, delta } = context
  const random = context.random || Math.random

  if (!enabled) {
    if (state.phase === 'walk') rest(state, elapsed, random, 0.8)
    return { position, heading: null, moving: false }
  }

  if (state.phase === 'rest') {
    if (!mayStart || elapsed < state.nextDecisionAt) return { position, heading: null, moving: false }
    const target = chooseTarget(position, home, others, random)
    if (!target) {
      rest(state, elapsed, random, 0.8)
      return { position, heading: null, moving: false }
    }
    state.phase = 'walk'
    state.target = target
  }

  const target = state.target
  if (!target) {
    rest(state, elapsed, random)
    return { position, heading: null, moving: false }
  }

  const dx = target.x - position.x
  const dz = target.z - position.z
  const remaining = Math.hypot(dx, dz)
  if (remaining <= ARRIVAL_DISTANCE) {
    rest(state, elapsed, random)
    return { position, heading: null, moving: false }
  }

  const step = Math.min(remaining, WANDER_SPEED * delta)
  const next = { x: position.x + (dx / remaining) * step, z: position.z + (dz / remaining) * step }
  const approachingNeighbor = others.some((other) => {
    const nextDistance = distance(next, other)
    return nextDistance < WALK_CLEARANCE && nextDistance < distance(position, other)
  })
  if (approachingNeighbor) {
    rest(state, elapsed, random, 0.8)
    return { position, heading: null, moving: false }
  }

  state.walkPhase += delta * 8.5
  return { position: next, heading: Math.atan2(dx, dz), moving: true }
}
