import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { exchangeCode, takePendingAuth } from '@/lib/auth'

const processedCallbacks = new Set<string>()

/** DAO OAuth 回调页（/oauth/callback）：校验 state → exchange → 回跳原页面。 */
export default function AuthCallbackPage() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const [err, setErr] = useState<string | null>(() => {
    const error = params.get('error')
    if (error) return error === 'access_denied' ? '已取消登录' : '登录失败，请重试'
    if (!params.get('code') || !params.get('state')) return '登录回调参数缺失，请重新登录'
    return null
  })

  useEffect(() => {
    if (err !== null) return
    const code = params.get('code')!
    const state = params.get('state')!
    const key = `${code}::${state}`
    if (processedCallbacks.has(key)) return
    processedCallbacks.add(key)

    void Promise.resolve()
      .then(() => {
        const pending = takePendingAuth()
        if (!pending) throw new Error('登录状态已过期，请重新登录')
        if (state !== pending.state) throw new Error('登录状态校验失败，请重新登录')
        return exchangeCode(code, pending.code_verifier).then(() =>
          navigate(pending.return_to, { replace: true }),
        )
      })
      .catch((e) => setErr(e instanceof Error ? e.message : '登录失败，请重新登录'))
  }, [err, params, navigate])

  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <div className="font-mono text-[13px] text-ink-2">
        {err ? (
          <div className="text-center">
            <div className="mb-4 text-signal">{err}</div>
            <a className="text-sky-400 underline decoration-dotted" href="/">返回首页重新登录</a>
          </div>
        ) : (
          '登录中…'
        )}
      </div>
    </div>
  )
}
