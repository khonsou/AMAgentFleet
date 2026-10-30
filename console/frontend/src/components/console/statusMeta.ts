export type LedColor = 'green' | 'amber' | 'cyan' | 'red' | 'signal' | 'off'

export interface StatusMeta { label: string; led: LedColor; blink?: boolean }

/** task 执行状态（全站唯一来源，取代各页重复的 STATUS_STYLE） */
export const TASK_STATUS: Record<string, StatusMeta> = {
  queued:     { label: '排队中', led: 'amber' },
  dispatched: { label: '已派发', led: 'amber' },
  running:    { label: '执行中', led: 'green', blink: true },
  success:    { label: '已完成', led: 'cyan' },
  error:      { label: '异常', led: 'red' },
  rejected:   { label: '已拒绝', led: 'red' },
}

/** Agent 定义状态 */
export const AGENT_STATUS: Record<string, StatusMeta> = {
  registered: { label: '已注册', led: 'cyan' },
  running:    { label: '运行中', led: 'green', blink: true },
  stopped:    { label: '已停止', led: 'off' },
  destroyed:  { label: '已删除', led: 'off' },
}

/** LOOP 领取/交付状态 */
export const LOOP_STATUS: Record<string, StatusMeta> = {
  claimed:   { label: '待执行', led: 'amber' },
  running:   { label: '执行中', led: 'green', blink: true },
  delivered: { label: '已交付', led: 'cyan' },
  returned:  { label: '已退回', led: 'amber' },
  accepted:  { label: '已采纳', led: 'green' },
  archived:  { label: '已归档', led: 'off' },
}

/** run/thread 状态 */
export const RUN_STATUS: Record<string, StatusMeta> = {
  running:     { label: '执行中', led: 'green', blink: true },
  success:     { label: '已完成', led: 'cyan' },
  error:       { label: '异常', led: 'red' },
  interrupted: { label: '已中断', led: 'amber' },
}

export function taskStatusMeta(status: string): StatusMeta {
  return TASK_STATUS[status] ?? { label: '未知状态', led: 'off' }
}
export function agentStatusMeta(status: string): StatusMeta {
  return AGENT_STATUS[status] ?? { label: status || '未知状态', led: 'off' }
}
export function loopStatusMeta(status: string): StatusMeta {
  return LOOP_STATUS[status] ?? { label: '未知状态', led: 'off' }
}
export function runStatusMeta(status: string): StatusMeta {
  return RUN_STATUS[status] ?? { label: '未知状态', led: 'off' }
}

/** loop 聚合状态（任务树整体态） */
export const LOOP_AGG: Record<string, StatusMeta> = {
  running: { label: '执行中', led: 'green', blink: true },
  error:   { label: '异常', led: 'red' },
  success: { label: '完成', led: 'cyan' },
  mixed:   { label: '部分完成', led: 'amber' },
}
