import { downloadArtifact as downloadProtocolArtifact, executeOperation, type OperationRequest } from '@/protocol/client'
import { executeGuiOperation } from '@/protocol/guiExecutor'

const BASE = '/api/console'

async function read<T>(operationId: string, request: OperationRequest = {}): Promise<T> {
  const response = await executeOperation<T>(operationId, request)
  return response.result
}

async function write<T>(operationId: string, request: OperationRequest = {}): Promise<T> {
  const response = await executeGuiOperation<T>(operationId, request)
  return response.result
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
  name?: string
}

export const api = {
  overview: () => read<Overview>('overview.get'),
  fleet: () => read<FleetRow[]>('fleet.threads'),
  thread: (tid: string) => read<ThreadDetail>('threads.get', { path_params: { tid } }),
  /** 产物下载地址（agent present_files 呈现的文件，经 Console 代理到网关） */
  artifactUrl: (tid: string, path: string) =>
    `${BASE}/threads/${tid}/artifacts/${path.split('/').map(encodeURIComponent).join('/')}`,
  downloadArtifact: (tid: string, path: string) =>
    downloadProtocolArtifact({ tid, path }),
  messages: (tid: string) => read<unknown>('threads.messages', { path_params: { tid } }),
  send: (tid: string, content: string, assistantId?: string) =>
    write<unknown>('threads.send', { path_params: { tid }, body: { content, ...(assistantId ? { assistant_id: assistantId } : {}) } }),
  create: (instruction?: string, assistantId?: string) =>
    write<{ thread_id: string }>('threads.create', { body: {
      ...(instruction?.trim() ? { instruction } : {}),
      ...(assistantId ? { assistant_id: assistantId } : {}),
    } }),
  destroy: (tid: string) => write<{ deleted: string }>('threads.destroy', { path_params: { tid } }),

  // ---- Registry（control-plane 代理，P2） ----
  agents: () => read<RegistryAgent[]>('agents.list'),
  agent: (name: string) => read<AgentDetail>('agents.get', { path_params: { name } }),
  agentUpdate: (name: string, yaml: unknown) =>
    write<RegistryAgent>('agents.update', { path_params: { name }, body: { yaml } }),
  agentDispatch: (name: string, input: string, modelProfile?: string, subagentModelPolicy = 'auto') =>
    write<{ task_id: number }>('agents.dispatch', { path_params: { name }, body: {
      input, source: 'web', model_profile: modelProfile || null,
      subagent_model_policy: subagentModelPolicy,
    } }),
  agentTasks: (name: string) => read<AgentTask[]>('tasks.list', { query: { agent: name, limit: 200 } }),
  allTasks: (limit = 200) => read<AgentTask[]>('tasks.list', { query: { limit } }),
  taskDetail: (id: number) => read<TaskDetail>('tasks.get', { path_params: { task_id: id } }),
  createTask: (input: string, assignee?: string, modelProfile?: string, subagentModelPolicy = 'auto') =>
    write<{ task_id: number }>('tasks.create', { body: {
      input, assignee, model_profile: modelProfile || null,
      subagent_model_policy: subagentModelPolicy,
    } }),
  taskClaim: (id: number, claimantType: string, claimant: string) =>
    write<{ loop_id: number }>('loops.claim', { path_params: { task_id: id }, body: { claimant_type: claimantType, claimant } }),
  taskAccept: (id: number, loopId: number, author = 'human') =>
    write<{ pool_status: string }>('tasks.accept', { path_params: { task_id: id }, body: { loop_id: loopId, author } }),
  taskClose: (id: number, author = 'human') =>
    write<{ pool_status: string }>('tasks.close', { path_params: { task_id: id }, query: { author } }),
  loopDeliver: (loopId: number, content: string, author = 'me') =>
    write<{ status: string }>('loops.deliver', { path_params: { loop_id: loopId }, body: { content, author } }),
  reviewContext: (threadId: string) =>
    read<ReviewContext>('threads.review_context', { path_params: { tid: threadId } }),
  loopRequeue: (loopId: number) =>
    write<{ status: string }>('loops.requeue', { path_params: { loop_id: loopId } }),
  loopResume: (loopId: number) =>
    write<{ status: string }>('loops.resume', { path_params: { loop_id: loopId } }),
  loopReturn: (loopId: number, comment: string, author = 'human') =>
    write<{ status: string }>('loops.return', { path_params: { loop_id: loopId }, body: { comment, author } }),
  agentInbox: (name: string, payload: string) =>
    write<{ inbox_id: number }>('inbox.post', { path_params: { name }, body: { payload, source: 'web' } }),
  agentStop: (name: string) =>
    write<{ status: string; paused_crons: string[] }>('agents.stop', { path_params: { name } }),
  agentStart: (name: string) =>
    write<{ status: string; resumed_crons: string[] }>('agents.start', { path_params: { name } }),
  agentDestroy: (name: string) =>
    write<void>('agents.destroy', { path_params: { name } }).then(() => undefined),
  agentCreate: (yaml: unknown) =>
    write<RegistryAgent>('agents.create', { body: { yaml } }),

  templates: () => read<Template[]>('templates.list'),
  templateRender: (body: Record<string, unknown>) =>
    write<{ yaml: Record<string, unknown> }>('templates.render', { body }),

  // ---- 模型 Profile 与原生 Managed Models（ADR-0028） ----
  models: () => read<ModelProfile[]>('models.list'),
  managedModels: () => read<{ models: ManagedModel[] }>('models.managed_list'),
  managedModelSave: (config: ManagedModelInput, expectedRevision: string | null) =>
    write<ManagedModel>('models.managed_save', { body: { config, expected_revision: expectedRevision } }),
  managedModelTest: (config: ManagedModelInput, expectedRevision: string | null) =>
    write<{ ok: boolean; message: string }>('models.managed_test', { body: { config, expected_revision: expectedRevision } }),

  // ---- FAP PAT 管理（ADR-0011 H6，Console 设置页） ----
  opsCredential: () => read<OpsCredentialStatus>('ops.credential_status'),
  opsCredentialRotate: () =>
    write<OpsCredentialStatus>('ops.credential_rotate'),
  pats: () => read<Pat[]>('pats.list'),
  patCreate: (name: string, scope: 'read' | 'write', expiresInDays?: number) =>
    write<Pat & { token: string }>('pats.issue', { body: { name, scopes: [scope], expires_in_days: expiresInDays ?? null } }),
  patRevoke: (id: string) =>
    write<{ status: string }>('pats.revoke', { path_params: { pat_id: id } }),
}

export interface ModelProfile {
  name: string
  model: string
  display_name: string
  description: string | null
  supports_thinking: boolean
  supports_reasoning_effort: boolean
}

export interface ManagedModel {
  name: string
  display_name: string
  model: string
  base_url?: string
  enabled: boolean
  supports_vision?: boolean
  context_window?: number | null
  max_tokens?: number | null
  revision?: string | null
  source: 'config' | 'managed'
  has_api_key?: boolean
  conflict?: boolean
}

export interface ManagedModelInput {
  name: string
  display_name: string
  provider: 'openai-compatible'
  model: string
  base_url: string
  api_key?: string
  enabled: boolean
  supports_vision: boolean
  context_window: number | null
  max_tokens: number | null
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
  description: string
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
  model_profile?: string | null
  model_snapshot?: { profile: string; model: string; display_name: string; source: string } | null
  model_selected_by?: string | null
  failure_kind?: 'recursion' | 'transient' | 'permanent' | null
  retry_count?: number
  continuation_count?: number
  next_retry_at?: string | null
  recovery_action?: 'retry' | 'resume' | null
  running_action?: 'retry' | 'resume' | null
  last_checkpoint_id?: string | null
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
  /** 指挥官已通过 agent_spawn 创建的子任务；失败恢复时用于核对进度，避免重复派发。 */
  children: AgentTask[]
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
  model_profile?: string | null
  subagent_model_policy?: 'auto' | 'inherit'
  input?: string
  tokens: number | null
  result_preview: string | null
  error: string | null
  created_at: string
  finished_at: string | null
}
