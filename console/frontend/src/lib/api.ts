import { getOauthSession, notifySessionExpired } from '@/lib/auth'

const BASE = '/api/console'

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = authHeaders(init?.headers)
  const resp = await fetch(`${BASE}${path}`, { ...init, headers })
  if (resp.status === 401) notifySessionExpired() // 令牌过期 → 回登录门
  if (!resp.ok) {
    const text = await resp.text()
    throw new Error(`${resp.status}: ${text.slice(0, 300)}`)
  }
  return resp.json()
}

function authHeaders(init?: HeadersInit): Headers {
  const headers = new Headers(init)
  // 员工身份（DAO JWT）随请求附带；认证未启用时 session 恒为 null，零影响
  const s = getOauthSession()
  if (s) headers.set('Authorization', `Bearer ${s.accessToken}`)
  return headers
}

async function download(url: string, filename: string): Promise<void> {
  const resp = await fetch(url, { headers: authHeaders() })
  if (resp.status === 401) {
    notifySessionExpired()
    return
  }
  if (!resp.ok) throw new Error(`产物下载失败（HTTP ${resp.status}）`)
  const objectUrl = URL.createObjectURL(await resp.blob())
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = filename
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
}

export interface Overview {
  threads: number
  runs: number
  running: number
  errored: number
  tokens_today: number
  tokens_total: number
  checkpoints: number
}

export interface FleetRow {
  thread_id: string
  display_name: string | null
  assistant_id: string | null
  thread_status: string
  created_at: string
  updated_at: string
  run_count: number
  total_tokens: number
  total_input_tokens: number
  total_output_tokens: number
  llm_call_count: number
  last_run_status: string | null
  last_run_at: string | null
  first_message: string | null
  checkpoint_count: number
}

export interface RunRow {
  run_id: string
  status: string
  model_name: string | null
  created_at: string
  updated_at: string
  total_input_tokens: number
  total_output_tokens: number
  total_tokens: number
  llm_call_count: number
  message_count: number
  error: string | null
  stop_reason: string | null
  first_human_message: string | null
  last_ai_message: string | null
}

export interface ThreadDetail {
  thread: Record<string, unknown>
  runs: RunRow[]
  checkpoints: number
}

export interface ChatMessage {
  role: 'human' | 'ai' | 'tool' | 'system' | 'unknown'
  content: string
}

export const api = {
  overview: () => req<Overview>('/overview'),
  fleet: () => req<FleetRow[]>('/fleet'),
  thread: (tid: string) => req<ThreadDetail>(`/threads/${tid}`),
  /** 产物下载地址（agent present_files 呈现的文件，经 Console 代理到网关） */
  artifactUrl: (tid: string, path: string) =>
    `${BASE}/threads/${tid}/artifacts/${path.split('/').map(encodeURIComponent).join('/')}`,
  downloadArtifact: (tid: string, path: string) =>
    download(
      `${BASE}/threads/${tid}/artifacts/${path.split('/').map(encodeURIComponent).join('/')}`,
      path.split('/').pop() || 'artifact',
    ),
  messages: (tid: string) => req<unknown>(`/threads/${tid}/messages`),
  send: (tid: string, content: string, assistantId?: string) =>
    req<unknown>(`/threads/${tid}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, ...(assistantId ? { assistant_id: assistantId } : {}) }),
    }),
  create: (instruction?: string, assistantId?: string) =>
    req<{ thread_id: string }>('/threads', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...(instruction?.trim() ? { instruction } : {}),
        ...(assistantId ? { assistant_id: assistantId } : {}),
      }),
    }),
  destroy: (tid: string) => req<{ deleted: string }>(`/threads/${tid}`, { method: 'DELETE' }),

  // ---- Registry（control-plane 代理，P2） ----
  agents: () => req<RegistryAgent[]>('/agents'),
  agent: (name: string) => req<AgentDetail>(`/agents/${name}`),
  agentUpdate: (name: string, yaml: unknown) =>
    req<RegistryAgent>(`/agents/${name}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ yaml }),
    }),
  agentDispatch: (name: string, input: string) =>
    req<{ task_id: number }>(`/agents/${name}/dispatch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ input, source: 'web' }),
    }),
  agentTasks: (name: string) => req<AgentTask[]>(`/agents/${name}/tasks`),
  allTasks: (limit = 200) => req<AgentTask[]>(`/tasks?limit=${limit}`),
  taskDetail: (id: number) => req<TaskDetail>(`/tasks/${id}`),
  createTask: (input: string, assignee?: string) =>
    req<{ task_id: number }>('/tasks', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ input, assignee }),
    }),
  taskClaim: (id: number, claimantType: string, claimant: string) =>
    req<{ loop_id: number }>(`/tasks/${id}/loops`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ claimant_type: claimantType, claimant }),
    }),
  taskAccept: (id: number, loopId: number, author = 'human') =>
    req<{ pool_status: string }>(`/tasks/${id}/accept`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ loop_id: loopId, author }),
    }),
  taskClose: (id: number, author = 'human') =>
    req<{ pool_status: string }>(`/tasks/${id}/close`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ author }),
    }),
  loopDeliver: (loopId: number, content: string, author = 'me') =>
    req<{ status: string }>(`/loops/${loopId}/deliver`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, author }),
    }),
  reviewContext: (threadId: string) =>
    req<ReviewContext>(`/threads/${threadId}/review-context`),
  loopRequeue: (loopId: number) =>
    req<{ status: string }>(`/loops/${loopId}/requeue`, { method: 'POST' }),
  loopReturn: (loopId: number, comment: string, author = 'human') =>
    req<{ status: string }>(`/loops/${loopId}/return`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ comment, author }),
    }),
  agentInbox: (name: string, payload: string) =>
    req<{ inbox_id: number }>(`/agents/${name}/inbox`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ payload, source: 'web' }),
    }),
  agentStop: (name: string) =>
    req<{ status: string; paused_crons: string[] }>(`/agents/${name}/stop`, { method: 'POST' }),
  agentStart: (name: string) =>
    req<{ status: string; resumed_crons: string[] }>(`/agents/${name}/start`, { method: 'POST' }),
  agentDestroy: (name: string) =>
    fetch(`${BASE}/agents/${name}`, { method: 'DELETE' }).then((r) => {
      if (!r.ok) throw new Error(`${r.status}: destroy failed`)
    }),
  agentCreate: (yaml: unknown) =>
    req<RegistryAgent>('/agents', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ yaml }),
    }),

  templates: () => req<Template[]>('/templates'),

  // ---- FAP PAT 管理（ADR-0011 H6，Console 设置页） ----
  opsCredential: () => req<OpsCredentialStatus>('/ops/credential'),
  opsCredentialRotate: () =>
    req<OpsCredentialStatus>('/ops/credential/rotate', { method: 'POST' }),
  pats: () => req<Pat[]>('/pats'),
  patCreate: (name: string, scope: 'read' | 'write', expiresInDays?: number) =>
    req<Pat & { token: string }>('/pats', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, scopes: [scope], expires_in_days: expiresInDays ?? null }),
    }),
  patRevoke: (id: string) =>
    req<{ status: string }>(`/pats/${id}`, { method: 'DELETE' }),
}

export interface Template {
  id: string
  name: string
  displayName: string
  description: string
}

export interface OpsCredentialStatus {
  agent: string
  system: true
  configured: boolean
  pat_id: string
  scopes: string[]
  version: number
  created_at: string
  rotated_at: string
  last_used_at: string | null
}

export interface Pat {
  id: string
  name: string
  scopes: string[]
  system?: boolean
  prefix: string
  expires_at: string | null
  revoked_at: string | null
  last_used_at: string | null
  created_at: string
}

export interface RegistryAgent {
  name: string
  display_name: string
  owner: string
  cost_center: string
  status: string
  config_version: number
  triggers: Record<string, unknown>
  identity: Record<string, unknown>
  budget: Record<string, unknown>
  synced_at: string | null
  updated_at: string
}

export interface AgentDetail {
  registry: RegistryAgent | null
  yaml: Record<string, unknown>
}

export interface ReviewContext {
  loop: Loop | null
  task?: { id: number; pool_status: string; input?: string }
}

export interface Loop {
  id: number
  task_id: number
  claimant_type: string
  claimant: string
  thread_id: string | null
  status: string
  deliverable: string | null
  /** 当前产出：thread 最新 AI 回复（展示用，派生）；deliverable 是交付时刻快照 */
  latest_output?: string | null
  /** 产物路径（run.delivery 提取，present_files 呈现 = 用户可下载意图） */
  artifacts?: string[] | null
  tokens: number | null
  version: number
  error: string | null
  created_at: string
}

export interface TaskComment {
  id: number
  author: string
  content: string
  loop_id: number | null
  created_at: string
}

export interface TaskDetail {
  task: AgentTask
  loops: Loop[]
  comments: TaskComment[]
}

export interface AgentTask {
  id: number
  agent_name: string
  source: string
  status: string
  thread_id: string | null
  latest_loop_id?: number | null
  latest_thread_id?: string | null
  latest_loop_status?: string | null
  parent_task_id: number | null
  depth: number
  pool_status?: string
  accepted_loop_id?: number | null
  input?: string
  tokens: number | null
  result_preview: string | null
  error: string | null
  created_at: string
  finished_at: string | null
}
