import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'

const mocks = vi.hoisted(() => ({ executeOperation: vi.fn(), executeGuiOperation: vi.fn(), downloadArtifact: vi.fn() }))
vi.mock('@/protocol/client', () => ({ executeOperation: mocks.executeOperation, downloadArtifact: mocks.downloadArtifact }))
vi.mock('@/protocol/guiExecutor', () => ({ executeGuiOperation: mocks.executeGuiOperation }))

describe('旧 Console 协议迁移', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.executeOperation.mockImplementation((operation_id: string) => Promise.resolve({ operation_id, status: 200, result: [], truncated: false }))
    mocks.executeGuiOperation.mockImplementation((operation_id: string) => Promise.resolve({ operation_id, status: 200, result: { task_id: 91, yaml: {} }, truncated: false }))
  })

  it('Registry 与总览读取使用 operation ID', async () => {
    await api.agents()
    await api.agent('worker-01')
    await api.overview()
    expect(mocks.executeOperation.mock.calls.map((call) => call[0])).toEqual(['agents.list', 'agents.get', 'overview.get'])
    expect(mocks.executeOperation.mock.calls[1][1]).toEqual({ path_params: { name: 'worker-01' } })
  })

  it('Agent 专属任务复用 tasks.list 查询参数', async () => {
    await api.agentTasks('worker-01')
    expect(mocks.executeOperation).toHaveBeenCalledWith('tasks.list', { query: { agent: 'worker-01', limit: 200 } })
  })

  it('写操作通过共享 GUI 确认桥并保持业务参数', async () => {
    await expect(api.createTask('统一写确认')).resolves.toEqual({ task_id: 91, yaml: {} })
    expect(mocks.executeGuiOperation).toHaveBeenCalledWith('tasks.create', { body: {
      input: '统一写确认', assignee: undefined, model_profile: null, subagent_model_policy: 'auto',
    } })
    expect(mocks.executeOperation).not.toHaveBeenCalled()
  })

  it('模板渲染和 human-only 写操作使用协议 operation', async () => {
    await api.templateRender({ templateId: 'website-reader' })
    await api.opsCredentialRotate()
    expect(mocks.executeGuiOperation.mock.calls.map((call) => call[0])).toEqual(['templates.render', 'ops.credential_rotate'])
  })

  it('产物下载复用共享二进制传输', async () => {
    await api.downloadArtifact('thread-1', 'reports/result.txt')
    expect(mocks.downloadArtifact).toHaveBeenCalledWith({ tid: 'thread-1', path: 'reports/result.txt' })
  })
})
