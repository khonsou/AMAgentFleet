import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { exchangeCode, takePendingAuth } from '@/lib/auth'

/** DAO OAuth 回调页（/oauth/callback）：校验 state → exchange → 回跳原页面。 */
export default function AuthCallbackPage() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    const error = params.get('error')
    const code = params.get('code')
    const state = params.get('state')
    if (error) {
      setErr(error === 'access_denied' ? '已取消登录' : '登录失败，请重试')
      return
    }
    const pending = takePendingAuth()
    if (!pending || !code || !state || state !== pending.state) {
      // state 缺失/不匹配：立即终止，不交换 code（规格 §9）
      setErr('登录状态校验失败，请重新登录')
      return
    }
    exchangeCode(code, pending.code_verifier)
      .then(() => {
        // 成功：清理地址栏 code/state，跳回同源 return_to
        window.history.replaceState(null, '', pending.return_to)
        navigate(pending.return_to, { replace: true })
      })
      .catch((e: Error) => setErr(e.message))
  }, [params, navigate])

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
