import { useEffect, useState } from 'react'
import { NavLink } from 'react-router'
import { cn } from '@/lib/utils'
import { Led } from './Status'

/** 机架顶栏：四个模块的切换排 + 自动刷新指示 + 时钟。
 *  视觉隐喻：机架式仪器的模块选择排，当前模块亮信号橙 LED。 */
const MODULES = [
  { to: '/', label: 'FLEET', end: true },
  { to: '/registry', label: 'REGISTRY' },
  { to: '/threads', label: 'THREADS' },
  { to: '/settings', label: 'SETTINGS' },
]

function Clock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])
  const pad = (n: number) => String(n).padStart(2, '0')
  const txt = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}  ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`
  return <span className="font-mono text-[11px] tracking-wider text-ink-3 tabular-nums">{txt}</span>
}

export default function Rack() {
  return (
    <nav className="flex h-[52px] items-center gap-7 border-b border-hairline bg-rack px-7">
      <div className="flex items-center gap-2.5">
        <div className="grid h-6 w-6 place-items-center rounded-[2px] bg-signal font-mono text-[11px] font-semibold text-signal-foreground">
          AM
        </div>
        <div className="font-mono text-xs font-semibold tracking-[0.14em] text-ink">
          AGENT&nbsp;FLEET <span className="font-normal text-ink-3">/ CONSOLE</span>
        </div>
      </div>
      <div className="ml-3 flex gap-0.5">
        {MODULES.map((m) => (
          <NavLink
            key={m.to}
            to={m.to}
            end={m.end}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-1.5 rounded-[2px] border border-transparent px-3.5 py-1.5',
                'font-mono text-[11px] tracking-[0.1em] text-ink-3 transition-colors hover:text-ink',
                isActive && 'border-hairline bg-panel text-ink',
              )
            }
          >
            {({ isActive }) => (
              <>
                <Led color={isActive ? 'signal' : 'off'} />
                {m.label}
              </>
            )}
          </NavLink>
        ))}
      </div>
      <div className="ml-auto flex items-center gap-4">
        <span className="inline-flex items-center gap-1.5 font-mono text-[11px] font-medium tracking-wider text-ink-2">
          <Led color="green" blink />
          AUTO
        </span>
        <Clock />
      </div>
    </nav>
  )
}
