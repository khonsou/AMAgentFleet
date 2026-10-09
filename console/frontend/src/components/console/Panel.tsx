import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

/** 仪器面板：哑光面 + 发丝描边 + 四角螺丝。 */
export function Panel({ className, screws = true, ...props }: HTMLAttributes<HTMLDivElement> & { screws?: boolean }) {
  return (
    <div
      className={cn('relative rounded-[3px] border border-hairline bg-panel', screws && 'panel-screws', className)}
      {...props}
    />
  )
}

/** 面板头：丝印标题栏 */
export function PanelHead({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('flex items-center justify-between gap-3 border-b border-hairline-soft px-4 py-2.5', className)}
      {...props}
    />
  )
}

export function PanelBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('p-4', className)} {...props} />
}

/** 凹陷行：面板内的次级容器（任务行、交付行、表格行 hover 面） */
export function InsetRow({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('rounded-[2px] border border-hairline-soft bg-inset px-3.5 py-2.5', className)}
      {...props}
    />
  )
}
