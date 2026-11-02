export const loopStatus = {
  claimed: { label: '已领取', tone: 'cyan' },
  running: { label: '执行中', tone: 'green' },
  returned: { label: '已退回', tone: 'amber' },
  delivered: { label: '待验收', tone: 'amber' },
  accepted: { label: '已采纳', tone: 'muted' },
  archived: { label: '已归档', tone: 'muted' },
  success: { label: '已结束', tone: 'muted' },
  error: { label: '异常', tone: 'red' },
}

function defaultExecution(task) {
  if (!task.loop) return null
  return {
    id: task.loop,
    taskId: task.id,
    agent: task.agent,
    model: 'deepseek-v4',
    thread: task.thread,
    status: task.acceptedLoop === task.loop ? 'accepted' : task.status,
    version: task.version || 1,
    runs: task.runs || 1,
    tokens: task.loopTokens ?? task.tokens,
    output: task.output,
    error: task.error || '',
    start: task.start,
    duration: task.duration,
    updated: task.updated,
  }
}

export function loopsFor(task) {
  const primary = defaultExecution(task)
  const competing = (task.competingLoops || []).map((loop) => ({
    version: 1,
    runs: 1,
    model: 'deepseek-v4',
    ...loop,
    taskId: task.id,
  }))
  return primary ? [primary, ...competing] : competing
}

export function defaultLoop(task) {
  const loops = loopsFor(task)
  return loops.find((loop) => loop.id === task.acceptedLoop)
    || loops.find((loop) => loop.id === task.loop)
    || loops[0]
    || null
}
