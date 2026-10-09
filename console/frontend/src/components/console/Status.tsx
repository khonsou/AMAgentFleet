import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/** LED 指示灯：状态的最小表达单元（点亮/呼吸/熄灭）。 */
export type LedColor = 'green' | 'amber' | 'cyan' | 'red' | 'signal' | 'off'

/* 必须用完整字面量类名：Tailwind 扫描不到动态拼接的 class，会整段裁掉 */
const LED_CLS: Record<LedColor, string> = {
  green: 'led-green',
  amber: 'led-amber',
  cyan: 'led-cyan',
  red: 'led-red',
  signal: 'bg-signal shadow-[0_0_6px_hsl(var(--signal)/0.6)]',
  off: 'led-off',
}

export function Led({ color = 'off', blink = false, className }: { color?: LedColor; blink?: boolean; className?: string }) {
  return <span className={cn('led', LED_CLS[color], blink && 'led-blink', className)} />
}

/** 状态点 + 等宽标签，替代原来的大面积染色 Badge。 */
export function StatusPill({ color, blink, children, className }: {
  color: LedColor; blink?: boolean; children: ReactNode; className?: string
}) {
  return (
    <span className={cn('inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap font-mono text-[11px] font-medium tracking-wider text-ink-2', className)}>
      <Led color={color} blink={blink} />
      {children}
    </span>
  )
}

interface StatusMeta { label: string; led: LedColor; blink?: boolean }

/** task 执行状态（全站唯一来源，取代各页重复的 STATUS_STYLE） */
export const TASK_STATUS: Record<string, StatusMeta> = {
  queued:     { label: '排队', led: 'amber' },
  dispatched: { label: '派发', led: 'amber' },
  running:    { label: '运行', led: 'green', blink: true },
  success:    { label: '完成', led: 'cyan' },
  error:      { label: '异常', led: 'red' },
  rejected:   { label: '拒绝', led: 'red' },
}

/** loop 领取/交付状态 */
export const LOOP_STATUS: Record<string, StatusMeta> = {
  claimed:   { label: '待执行', led: 'amber' },
  running:   { label: '执行中', led: 'green', blink: true },
  delivered: { label: '已交付', led: 'cyan' },
  returned:  { label: '已打回', led: 'amber', blink: true },
  accepted:  { label: '已采纳', led: 'green' },
  archived:  { label: '已归档', led: 'off' },
}

/** run/thread 状态 */
export const RUN_STATUS: Record<string, StatusMeta> = {
  running:     { label: '运行中', led: 'green', blink: true },
  success:     { label: '已完成', led: 'cyan' },
  error:       { label: '异常', led: 'red' },
  interrupted: { label: '已中断', led: 'amber' },
}

export function taskStatusMeta(status: string): StatusMeta {
  return TASK_STATUS[status] ?? { label: status, led: 'off' }
}
export function loopStatusMeta(status: string): StatusMeta {
  return LOOP_STATUS[status] ?? { label: status, led: 'off' }
}
export function runStatusMeta(status: string): StatusMeta {
  return RUN_STATUS[status] ?? { label: status, led: 'off' }
}

/** loop 聚合状态（任务树整体态） */
export const LOOP_AGG: Record<string, StatusMeta> = {
  running: { label: '执行中', led: 'green', blink: true },
  error:   { label: '异常', led: 'red' },
  success: { label: '完成', led: 'cyan' },
  mixed:   { label: '部分完成', led: 'amber' },
}
