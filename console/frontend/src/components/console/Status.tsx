import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import type { LedColor } from './statusMeta'

export type { LedColor } from './statusMeta'

/** LED 指示灯：状态的最小表达单元（点亮/呼吸/熄灭）。 */
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
