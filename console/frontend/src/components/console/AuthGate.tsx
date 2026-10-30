import { useEffect, useState, type ReactNode } from 'react'
import {
  AUTH_ENABLED, getOauthSession, startOauthLogin, subscribeAuth,
} from '@/lib/auth'

/** 员工登录门（DAO OAuth）：未启用认证时透明通过；启用后无会话时整页引导登录。 */
export default function AuthGate({ children }: { children: ReactNode }) {
  const [, force] = useState(0)
  useEffect(() => subscribeAuth(() => force((n) => n + 1)), [])

  if (!AUTH_ENABLED) return <>{children}</>
  if (getOauthSession()) return <>{children}</>

  return (
    <div className="flex min-h-[70vh] flex-col items-center justify-center gap-5">
      <div className="silk text-center text-[13px] text-ink-2">
        AM AGENT FLEET
        <div className="mt-2 text-[21px] font-semibold tracking-tight text-ink">登录 Fleet Console</div>
        <div className="mt-1.5 text-[12px]">使用公司统一账号（DAO）继续</div>
      </div>
      <button
        onClick={() => void startOauthLogin('/')}
        className="rounded-[2px] border border-led-green/50 bg-led-green/10 px-6 py-2.5 font-mono text-[13px] text-led-green transition-colors hover:bg-led-green/20"
      >
        统一账号登录 →
      </button>
    </div>
  )
}
