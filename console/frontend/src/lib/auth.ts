/**
 * DAO（angrymiao-auth）统一账号登录 —— Authorization Code + PKCE，纯前端整页跳转。
 * 规格与 timeline 项目一致（docs/oauth-auth-integration.md）：纯内存会话、
 * code_verifier/token 不落 localStorage、refresh token 不保存（刷新页面重新登录
 * 是预期行为）；return_to 同源校验；错误不回显 code/URL。
 *
 * 身份分工（P4）：员工（人）= DAO OAuth JWT；第三方 agent = PAT（FAP 面）；
 * 本模块只服务「人」这一侧。
 */

const AUTH_ORIGIN: string = import.meta.env.VITE_AUTH_ORIGIN || 'https://auth.angrymiao.com'
const CLIENT_ID: string = import.meta.env.VITE_OAUTH_CLIENT_ID || 'am-agent-fleet'
const SCOPE = 'profile phone'
export const AUTH_ENABLED: boolean = import.meta.env.VITE_AUTH_ENABLED === '1'

const REDIRECT_URI = `${window.location.origin}/oauth/callback`
const PENDING_KEY = 'fleet:oauth:pending'

export interface OauthSession {
  accessToken: string
  expiresAt: number
  scope: string
}

interface PendingAuth {
  state: string
  code_verifier: string
  return_to: string
}

let session: OauthSession | null = null
const listeners = new Set<() => void>()
const emit = () => listeners.forEach((f) => f())

export function subscribeAuth(fn: () => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

export function getOauthSession(): OauthSession | null {
  return session && session.expiresAt > Date.now() ? session : null
}

/** API 401 时调用：清会话并让所有 AuthGate 回到登录页 */
export function notifySessionExpired(): void {
  session = null
  emit()
}

export function oauthLogout(): void {
  session = null
  emit()
}

const b64url = (bytes: Uint8Array): string =>
  btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')

const randomString = (nbytes: number): string => b64url(crypto.getRandomValues(new Uint8Array(nbytes)))

async function sha256b64url(text: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
  return b64url(new Uint8Array(digest))
}

/** return_to 同源校验：只接受站内路径（可含 query/hash），其余回退 '/' */
export function sanitizeReturnTo(raw: unknown): string {
  if (typeof raw !== 'string' || raw.length === 0) return '/'
  if (!raw.startsWith('/') || raw.startsWith('//') || raw.includes('\\')) return '/'
  return raw
}

export async function startOauthLogin(returnTo?: string): Promise<void> {
  const return_to = sanitizeReturnTo(
    returnTo ?? `${window.location.pathname}${window.location.search}${window.location.hash}`,
  )
  const state = randomString(16)
  const code_verifier = randomString(48)
  const code_challenge = await sha256b64url(code_verifier)
  try {
    sessionStorage.setItem(PENDING_KEY, JSON.stringify({ state, code_verifier, return_to }))
  } catch {
    // 存储不可用：回调会因 pending 缺失而失败并提示重新登录
  }
  const q = new URLSearchParams({
    client_id: CLIENT_ID,
    response_type: 'code',
    redirect_uri: REDIRECT_URI,
    scope: SCOPE,
    state,
    code_challenge,
    code_challenge_method: 'S256',
  })
  window.location.assign(`${AUTH_ORIGIN}/oauth/authorize?${q.toString()}`)
}

/** 读取并删除 pending OAuth 状态（回调页一次性消费；形状非法按缺失处理） */
export function takePendingAuth(): PendingAuth | null {
  try {
    const raw = sessionStorage.getItem(PENDING_KEY)
    sessionStorage.removeItem(PENDING_KEY)
    if (!raw) return null
    const p = JSON.parse(raw) as Partial<PendingAuth>
    if (
      typeof p.state === 'string' && p.state.length > 0 &&
      typeof p.code_verifier === 'string' && p.code_verifier.length > 0 &&
      typeof p.return_to === 'string'
    ) {
      return { state: p.state, code_verifier: p.code_verifier, return_to: sanitizeReturnTo(p.return_to) }
    }
    return null
  } catch {
    return null
  }
}

export async function exchangeCode(code: string, codeVerifier: string): Promise<void> {
  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    client_id: CLIENT_ID,
    code,
    redirect_uri: REDIRECT_URI,
    code_verifier: codeVerifier,
  })
  let res: Response
  try {
    res = await fetch(`${AUTH_ORIGIN}/api/oauth/token`, {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    })
  } catch {
    throw new Error('网络不可达，请稍后重试')
  }
  if (!res.ok) {
    throw new Error(
      res.status === 400 ? '授权码无效或已过期，请重新登录' : `登录服务异常（HTTP ${res.status}），请稍后重试`,
    )
  }
  const j = (await res.json()) as {
    access_token?: unknown
    expires_in?: unknown
    scope?: unknown
  }
  if (typeof j.access_token !== 'string' || !j.access_token) {
    throw new Error('登录服务响应异常，请重新登录')
  }
  const expiresIn = typeof j.expires_in === 'number' && j.expires_in > 0 ? j.expires_in : 3600
  session = {
    accessToken: j.access_token,
    expiresAt: Date.now() + expiresIn * 1000,
    scope: typeof j.scope === 'string' ? j.scope : '',
  }
  emit()
}
