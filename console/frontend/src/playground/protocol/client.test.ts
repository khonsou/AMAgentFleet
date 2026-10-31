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
})
