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
})
