import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AgentTaskConversation from './AgentTaskConversation'
import type { AgentView, FleetTask, TaskConversation as Conversation } from '../core/types'

const mocks = vi.hoisted(() => ({ execute: vi.fn(), onChanged: vi.fn(), onClose: vi.fn() }))
vi.mock('@/protocol/useOperations', () => ({ useOperations: () => ({ execute: mocks.execute, busy: false }) }))

function task(id: number, input: string): FleetTask {
  return {
    id, input, agent_name: 'worker-01', status: 'running', pool_status: 'open',
    latest_loop_id: id + 100, latest_loop_status: 'running', latest_thread_id: `thread-${id}`,
    latest_deliverable: null, latest_message: null, claimants: [], result_preview: null,
    error: null, created_at: '2026-11-02T00:00:00Z', parent_task_id: null,
  }
}

function conversation(taskValue: FleetTask): Conversation {
  return {
    task: taskValue,
    participants: [{ id: 'agent:worker-01', name: 'worker-01', type: 'agent', loop_id: taskValue.id + 100, thread_id: `thread-${taskValue.id}`, status: 'running' }],
    messages: [], outputs: [], children: [], loops: [],
  }
}

function agent(tasks: FleetTask[]): AgentView {
  return {
    id: 'worker-01', name: 'WORKER', role: 'Worker', registryStatus: 'registered',
    status: tasks.length ? 'working' : 'idle', tasks, latestTask: tasks[0] ?? null, bubble: null,
  }
}

describe('AgentTaskConversation 普通 Agent 的 Task 路由', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(cleanup)

  it('没有可继续 Task 时，首条消息创建指派 Task 并进入同一群聊', async () => {
    mocks.execute.mockImplementation((operation: string, request?: { path_params?: { task_id?: number } }) => {
      if (operation === 'tasks.create') return Promise.resolve({ result: { task_id: 41 } })
      if (operation === 'tasks.conversation') {
        const id = request?.path_params?.task_id || 41
        return Promise.resolve({ result: conversation(task(id, id === 41 ? '完成新工作' : `Task ${id}`)) })
      }
      throw new Error(`unexpected operation: ${operation}`)
    })
    const user = userEvent.setup()
    render(<AgentTaskConversation agent={agent([])} agents={[]} onClose={mocks.onClose} onChanged={mocks.onChanged} />)

    await user.type(screen.getByLabelText('给 WORKER 的新 Task 指令'), '完成新工作')
    await user.click(screen.getByRole('button', { name: '创建 Task 并开始对话' }))

    expect(mocks.execute).toHaveBeenCalledWith('tasks.create', {
      body: {
        input: '完成新工作', assignee: 'worker-01', parent_task_id: null,
        model_profile: null, subagent_model_policy: 'auto',
      },
    })
    await waitFor(() => expect(mocks.execute).toHaveBeenCalledWith('tasks.conversation', { path_params: { task_id: 41 } }))
    expect(await screen.findByText('完成新工作')).toBeInTheDocument()
    expect(mocks.onChanged).toHaveBeenCalled()
    expect(mocks.execute.mock.calls.filter((call) => call[0] === 'threads.create')).toHaveLength(0)
  })

  it('已有多个 Task 时默认最近活动项，并允许切换同一 Task 对话', async () => {
    const tasks = [task(12, '最近任务'), task(11, '较早任务')]
    mocks.execute.mockImplementation((operation: string, request?: { path_params?: { task_id?: number } }) => {
      if (operation !== 'tasks.conversation') throw new Error(`unexpected operation: ${operation}`)
      const id = request?.path_params?.task_id || 12
      return Promise.resolve({ result: conversation(task(id, id === 12 ? '最近任务' : '较早任务')) })
    })
    const user = userEvent.setup()
    render(<AgentTaskConversation agent={agent(tasks)} agents={[]} onClose={mocks.onClose} onChanged={mocks.onChanged} />)

    expect(await screen.findByText('最近任务')).toBeInTheDocument()
    expect(mocks.execute).toHaveBeenCalledWith('tasks.conversation', { path_params: { task_id: 12 } })
    await user.selectOptions(screen.getByLabelText('当前 Task'), '11')
    expect(await screen.findByText('较早任务')).toBeInTheDocument()
    expect(mocks.execute).toHaveBeenCalledWith('tasks.conversation', { path_params: { task_id: 11 } })
    expect(mocks.execute.mock.calls.filter((call) => ['threads.create', 'threads.messages'].includes(call[0]))).toHaveLength(0)
  })
})
