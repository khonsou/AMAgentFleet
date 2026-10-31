export interface Claimant {
  loop_id: number
  claimant_type: 'agent' | 'human'
  claimant: string
  status: string
  thread_id: string | null
}
export interface FleetTask {
  id: number
  input: string
  agent_name: string
  status: string
  pool_status: 'open' | 'review' | 'done'
  latest_loop_id: number | null
  latest_loop_status: string | null
  latest_thread_id: string | null
  latest_deliverable: string | null
  latest_message: string | null
  claimants: Claimant[] | null
  result_preview: string | null
  error: string | null
  created_at: string
  updated_at?: string
  parent_task_id: number | null
}
export interface RegistryAgent {
  name: string
  display_name: string
  status: string
  identity?: Record<string, unknown>
}
export type AgentVisualStatus = 'working' | 'waiting' | 'error' | 'idle' | 'offline'
export interface AgentView {
  id: string
  name: string
  role: string
  registryStatus: string
  status: AgentVisualStatus
  tasks: FleetTask[]
  latestTask: FleetTask | null
  bubble: string | null
}
export interface SceneViewModel {
  agents: AgentView[]
  tasks: FleetTask[]
  openTasks: number
  activeAgents: number
  generatedAt: string
}
export interface ConversationMessage {
  id: string
  kind: 'message' | 'comment' | 'subagent'
  actor: string
  content: string
  created_at: string
  loop_id?: number
  thread_id?: string
  run_id?: string
  seq?: number
  event_type?: string
}
export interface ConversationOutput {
  id: string
  loop_id: number
  actor: string
  content: string
  status: string
  version: number
  created_at: string
  thread_id: string
}
export interface ConversationParticipant {
  id: string
  name: string
  type: 'agent' | 'human'
  loop_id: number
  thread_id: string | null
  status: string
}
export interface TaskConversation {
  task: FleetTask
  participants: ConversationParticipant[]
  messages: ConversationMessage[]
  outputs: ConversationOutput[]
  children: FleetTask[]
  loops: Array<{
    id: number
    claimant: string
    claimant_type: 'agent' | 'human'
    status: string
    thread_id: string | null
    deliverable: string | null
    error: string | null
    version: number
  }>
}
