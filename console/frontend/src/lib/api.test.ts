import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'

const mocks = vi.hoisted(() => ({ executeOperation: vi.fn() }))
vi.mock('@/protocol/client', () => ({ executeOperation: mocks.executeOperation }))

describe('旧 Console 只读协议迁移', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.executeOperation.mockImplementation((operation_id: string) => Promise.resolve({ operation_id, status: 200, result: [], truncated: false }))
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

  it('A2 阶段写操作仍走原有 Console 通道', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ task_id: 91 }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(api.createTask('只读迁移期间保持写路径')).resolves.toEqual({ task_id: 91 })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/console/tasks')
    expect(mocks.executeOperation).not.toHaveBeenCalled()
  })
})
