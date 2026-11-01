import { getOauthSession, notifySessionExpired } from '@/lib/auth'

export interface OperationDefinition {
  id: string
  method: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH'
  path: string
  surface: 'control-plane' | 'console' | 'gateway'
  description: string
  write?: boolean
  confirmation?: 'standard' | 'human_only'
  query?: Record<string, unknown>
  body?: Record<string, unknown>
  secret_fields?: string[]
  timeout_seconds?: number
}
export interface ProtocolDocument {
  protocol: string
  version: string
  operations: OperationDefinition[]
}
export interface OperationRequest {
  path_params?: Record<string, unknown>
  query?: Record<string, unknown>
  body?: Record<string, unknown>
  confirmed?: boolean
  idempotency_key?: string
}
export interface OperationResult<T = unknown> {
  operation_id: string
  status: number
  result: T
  truncated: boolean
}

let protocolPromise: Promise<ProtocolDocument> | null = null

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  const session = getOauthSession()
  if (session) headers.set('Authorization', `Bearer ${session.accessToken}`)
  const response = await fetch(path, { ...init, headers })
  if (response.status === 401) notifySessionExpired()
  const text = await response.text()
  let payload: unknown = null
  try { payload = text ? JSON.parse(text) : null } catch { payload = text }
  if (!response.ok) throw new Error(typeof payload === 'string' ? payload : JSON.stringify(payload))
  return payload as T
}

export function getProtocol(force = false): Promise<ProtocolDocument> {
  if (force || !protocolPromise) {
    protocolPromise = req<ProtocolDocument>('/api/console/operations/protocol').catch((error) => {
      protocolPromise = null
      throw error
    })
  }
  return protocolPromise
}

export async function executeOperation<T = unknown>(operationId: string, request: OperationRequest = {}): Promise<OperationResult<T>> {
  const envelope = await req<OperationResult<T>>('/api/console/operations/execute', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ operation_id: operationId, ...request }),
  })
  if (envelope.status >= 400) {
    const detail = typeof envelope.result === 'string' ? envelope.result : JSON.stringify(envelope.result)
    throw new Error(`${envelope.status}: ${detail}`)
  }
  return envelope
}

export async function downloadArtifact(pathParams: Record<string, unknown>): Promise<void> {
  const threadId = encodeURIComponent(String(pathParams.tid || ''))
  const artifactPath = String(pathParams.path || '')
  if (!threadId || !artifactPath) throw new Error('下载产物需要 tid 与 path')
  const path = artifactPath.split('/').filter(Boolean).map(encodeURIComponent).join('/')
  const headers = new Headers()
  const session = getOauthSession()
  if (session) headers.set('Authorization', `Bearer ${session.accessToken}`)
  const response = await fetch(`/api/console/threads/${threadId}/artifacts/${path}`, { headers })
  if (response.status === 401) notifySessionExpired()
  if (!response.ok) throw new Error(`产物下载失败（HTTP ${response.status}）`)
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = artifactPath.split('/').pop() || 'artifact'
  anchor.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function operationKey(intent: string): string {
  const id = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `gui-${intent}-${id}`
}
