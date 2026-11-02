import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AgentConversation from './AgentConversation'
import type { AgentView } from '../core/types'

const mocks = vi.hoisted(() => ({ execute: vi.fn(), onChanged: vi.fn() }))
vi.mock('@/protocol/useOperations', () => ({ useOperations: () => ({ execute: mocks.execute, busy: false }) }))

const agent: AgentView = {
  id: 'commander', name: 'Commander', role: 'Orchestration', registryStatus: 'registered',
  status: 'idle', tasks: [], latestTask: null, bubble: null,
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

describe('AgentConversation 异步人类消息', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
  })
  afterEach(cleanup)

  it('立即上屏并允许连续发送，首次只创建一个 Thread', async () => {
    const create = deferred<{ operation_id: string; status: number; result: { thread_id: string }; truncated: boolean }>()
    const send = deferred<{ operation_id: string; status: number; result: Record<string, never>; truncated: boolean }>()
    mocks.execute.mockImplementation((operation: string) => {
      if (operation === 'threads.create') return create.promise
      if (operation === 'threads.send') return send.promise
      if (operation === 'threads.messages') return Promise.resolve({ result: [] })
      throw new Error(`unexpected operation: ${operation}`)
    })
    const user = userEvent.setup()
    render(<AgentConversation agent={agent} onChanged={mocks.onChanged} />)
    const input = screen.getByLabelText('给 Commander 的消息')

    await user.type(input, '第一条消息')
    await user.click(screen.getByRole('button', { name: '开始对话' }))
    expect(screen.getByText('第一条消息')).toBeInTheDocument()
    expect(input).toHaveValue('')
    expect(screen.getByText('YOU · SENDING')).toBeInTheDocument()

    await user.type(input, '第二条消息')
    expect(screen.getByRole('button', { name: '开始对话' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: '开始对话' }))
    expect(screen.getByText('第二条消息')).toBeInTheDocument()
    expect(screen.queryByRole('alertdialog')).toBeNull()

    await act(async () => { create.resolve({ operation_id: 'threads.create', status: 200, result: { thread_id: 'shared-thread' }, truncated: false }) })
    await waitFor(() => expect(mocks.execute).toHaveBeenCalledWith('threads.send', expect.objectContaining({
      path_params: { tid: 'shared-thread' }, body: expect.objectContaining({ content: '第二条消息' }),
    })))
    await act(async () => { send.resolve({ operation_id: 'threads.send', status: 200, result: {}, truncated: false }) })
    expect(mocks.execute.mock.calls.filter((call) => call[0] === 'threads.create')).toHaveLength(1)
    expect(mocks.execute.mock.calls.filter((call) => call[0] === 'threads.send')).toHaveLength(1)
    expect(mocks.onChanged).toHaveBeenCalled()
  })

  it('Fleet Ops 恢复全局共享 Thread 并每 5 秒刷新消息', async () => {
    const ops = { ...agent, id: 'fleet-ops-01', name: 'Fleet Ops' }
    window.localStorage.setItem('fleet-ops-thread-id', 'shared-ops-thread')
    mocks.execute.mockImplementation((operation: string) => {
      if (operation === 'threads.get') return Promise.resolve({ result: { thread: { assistant_id: 'fleet-ops-01' } } })
      if (operation === 'threads.messages') return Promise.resolve({ result: [] })
      throw new Error(`unexpected operation: ${operation}`)
    })
    vi.useFakeTimers()
    try {
      render(<AgentConversation agent={ops} onChanged={mocks.onChanged} />)
      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      expect(mocks.execute).toHaveBeenCalledWith('threads.get', { path_params: { tid: 'shared-ops-thread' } })
      expect(mocks.execute).toHaveBeenCalledWith('threads.messages', { path_params: { tid: 'shared-ops-thread' } })
      expect(mocks.execute.mock.calls.filter((call) => call[0] === 'threads.create')).toHaveLength(0)

      const before = mocks.execute.mock.calls.filter((call) => call[0] === 'threads.messages').length
      await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
      expect(mocks.execute.mock.calls.filter((call) => call[0] === 'threads.messages').length).toBeGreaterThan(before)
    } finally {
      vi.useRealTimers()
    }
  })
})
