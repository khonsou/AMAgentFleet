import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TaskConversation from './TaskConversation'
import type { TaskConversation as Conversation } from '../core/types'

const mocks = vi.hoisted(() => ({ execute: vi.fn(), onChanged: vi.fn(), onClose: vi.fn() }))
vi.mock('@/protocol/useOperations', () => ({ useOperations: () => ({ execute: mocks.execute, busy: false }) }))

const baseConversation: Conversation = {
  task: {
    id: 11, input: '测试 Task', agent_name: 'commander', status: 'running', pool_status: 'review',
    latest_loop_id: 21, latest_loop_status: 'delivered', latest_thread_id: 'thread-a',
    latest_deliverable: null, latest_message: null, claimants: [], result_preview: null, error: null,
    created_at: '2026-11-01T00:00:00Z', parent_task_id: null,
  },
  participants: [{ id: 'agent:commander', name: 'commander', type: 'agent', loop_id: 21, thread_id: 'thread-a', status: 'delivered' }],
  messages: [], outputs: [], children: [], loops: [],
}

describe('TaskConversation 异步群聊', () => {
  beforeEach(() => { vi.clearAllMocks() })
  afterEach(cleanup)

  it('人类输入立即进入群聊并允许继续发送', async () => {
    const pending: Array<(value: unknown) => void> = []
    mocks.execute.mockImplementation((operation: string) => {
      if (operation === 'tasks.conversation') return Promise.resolve({ result: baseConversation })
      if (operation === 'threads.send') return new Promise((resolve) => { pending.push(resolve) })
      throw new Error(`unexpected operation: ${operation}`)
    })
    const user = userEvent.setup()
    render(<TaskConversation taskId={11} agents={[]} onClose={mocks.onClose} onChanged={mocks.onChanged} />)
    expect(await screen.findByText('TASK GROUP CHAT / #11')).toBeInTheDocument()
    const input = screen.getByLabelText('群聊消息')

    await user.type(input, '第一条群聊消息')
    await user.click(screen.getByRole('button', { name: '发送' }))
    expect(screen.getByText('第一条群聊消息')).toBeInTheDocument()
    expect(input).toHaveValue('')
    expect(screen.getByText('human · SENDING')).toBeInTheDocument()

    await user.type(input, '第二条群聊消息')
    expect(screen.getByRole('button', { name: '发送' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: '发送' }))
    expect(screen.getByText('第二条群聊消息')).toBeInTheDocument()
    expect(screen.queryByRole('alertdialog')).toBeNull()

    await act(async () => { pending.forEach((resolve) => resolve({ status: 200, result: {}, operation_id: 'threads.send', truncated: false })) })
    await waitFor(() => expect(mocks.onChanged).toHaveBeenCalled())
    expect(mocks.execute.mock.calls.filter((call) => call[0] === 'threads.send')).toHaveLength(2)
  })
})
