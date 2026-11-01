import { beforeEach, describe, expect, it, vi } from 'vitest'
import { executeOperation, getProtocol } from './client'

describe('Shared Protocol Client', () => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('所有客户端共享同一份 operation 清单', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ protocol: 'am-fleet-ops', version: '1', operations: [] }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const doc = await getProtocol(true)
    expect(doc.protocol).toBe('am-fleet-ops')
    expect(fetchMock.mock.calls[0][0]).toBe('/api/console/operations/protocol')
  })

  it('写操作把确认与幂等键交给服务端再次校验', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ operation_id: 'tasks.comment', status: 200, result: { status: 'ok' }, truncated: false }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await executeOperation('tasks.comment', { path_params: { task_id: 11 }, body: { content: 'hello' }, confirmed: true, idempotency_key: 'gui-1' })
    const payload = JSON.parse(fetchMock.mock.calls[0][1].body)
    expect(payload).toMatchObject({ operation_id: 'tasks.comment', confirmed: true, idempotency_key: 'gui-1' })
    expect(payload.path_params.task_id).toBe(11)
  })

  it('空错误响应明确显示 HTTP 状态和后端端口', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(new Response('null', { status: 502 }))))
    await expect(executeOperation('tasks.list')).rejects.toThrow('HTTP 502')
    await expect(executeOperation('tasks.list')).rejects.toThrow('8001/8002/8003')
  })

  it('网络连接失败时提示不能只启动 Vite', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('fetch failed')))
    await expect(getProtocol(true)).rejects.toThrow('8001/8002/8003')
    await expect(getProtocol(true)).rejects.toThrow('不能只有前端 Vite')
  })
})
