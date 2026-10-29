import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AgentTask, FleetRow, Loop, RegistryAgent, TaskDetail } from '@/lib/api'
import Home from './Home'
import AgentDetailPage from './AgentDetail'
import TaskDetailPage from './TaskDetail'
import ThreadDetailPage from './ThreadDetail'
import ThreadsPage from './Threads'

const apiMock = vi.hoisted(() => ({
  allTasks: vi.fn(),
  agents: vi.fn(),
  models: vi.fn(),
  createTask: vi.fn(),
  agentDispatch: vi.fn(),
  taskDetail: vi.fn(),
  taskClaim: vi.fn(),
  taskClose: vi.fn(),
  taskAccept: vi.fn(),
  loopReturn: vi.fn(),
  loopDeliver: vi.fn(),
  loopRequeue: vi.fn(),
  loopResume: vi.fn(),
  agent: vi.fn(),
  agentTasks: vi.fn(),
  agentUpdate: vi.fn(),
  agentStop: vi.fn(),
  agentStart: vi.fn(),
  agentDestroy: vi.fn(),
  thread: vi.fn(),
  messages: vi.fn(),
  reviewContext: vi.fn(),
  send: vi.fn(),
  create: vi.fn(),
  fleet: vi.fn(),
  destroy: vi.fn(),
  artifactUrl: vi.fn(),
}))

vi.mock('@/lib/api', () => ({ api: apiMock }))

function makeTask(overrides: Partial<AgentTask> = {}): AgentTask {
  return {
    id: 11,
    agent_name: 'unassigned',
    source: 'web',
    status: 'queued',
    thread_id: null,
    parent_task_id: null,
    depth: 0,
    pool_status: 'open',
    accepted_loop_id: null,
    input: '整理发布简报',
    tokens: 0,
    result_preview: null,
    error: null,
    created_at: '2026-10-27T06:00:00Z',
    finished_at: null,
    ...overrides,
  }
}

function makeLoop(overrides: Partial<Loop> = {}): Loop {
  return {
    id: 21,
    task_id: 11,
    claimant_type: 'agent',
    claimant: 'worker',
    thread_id: 'loop-thread',
    status: 'delivered',
    deliverable: '交付内容',
    latest_output: '交付内容',
    artifacts: [],
    tokens: 12,
    version: 1,
    error: null,
    created_at: '2026-10-27T06:01:00Z',
    ...overrides,
  }
}

function makeAgent(overrides: Partial<RegistryAgent> = {}): RegistryAgent {
  return {
    name: 'worker',
    display_name: 'Worker',
    owner: 'qa',
    cost_center: 'QA-1',
    status: 'registered',
    config_version: 3,
    triggers: {},
    identity: {},
    budget: {},
    synced_at: null,
    updated_at: '2026-10-27T06:00:00Z',
    ...overrides,
  }
}

function makeTaskDetail(overrides: Partial<TaskDetail> = {}): TaskDetail {
  return {
    task: makeTask(),
    loops: [],
    comments: [],
    children: [],
    ...overrides,
  }
}

function renderPage(initialEntry: string, route: string, element: React.ReactNode) {
  window.history.replaceState({}, '', initialEntry)
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path={route} element={element} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  apiMock.allTasks.mockResolvedValue([])
  apiMock.agents.mockResolvedValue([])
  apiMock.models.mockResolvedValue([])
  apiMock.createTask.mockResolvedValue({ task_id: 99 })
  apiMock.agentDispatch.mockResolvedValue({ task_id: 99 })
  apiMock.taskDetail.mockResolvedValue(makeTaskDetail())
  apiMock.agent.mockResolvedValue({ registry: makeAgent(), yaml: { kind: 'Agent' } })
  apiMock.agentTasks.mockResolvedValue([])
  apiMock.thread.mockResolvedValue({
    thread: { agent_name: 'worker', status: 'success' },
    runs: [],
    checkpoints: 0,
  })
  apiMock.messages.mockResolvedValue([])
  apiMock.create.mockResolvedValue({ thread_id: 'fleet-ops-thread' })
  apiMock.reviewContext.mockResolvedValue({ loop: null })
  apiMock.fleet.mockResolvedValue([])
})

afterEach(() => {
  cleanup()
})

describe('定义与实例导航', () => {
  it('首页突出 Task 与 Output，经最新 Loop 进入 Thread，Agent 定义仅作辅助引用', async () => {
    const task = makeTask({
      agent_name: 'worker',
      status: 'success',
      pool_status: 'done',
      result_preview: '这是最终交付摘要',
      thread_id: 'legacy-task-thread',
      latest_loop_id: 21,
      latest_thread_id: 'latest-loop-thread',
      latest_loop_status: 'accepted',
    })
    apiMock.allTasks.mockResolvedValue([task])

    const { container } = renderPage('/', '/', <Home />)

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /已验收/ }))
    await screen.findAllByText('整理发布简报')
    expect(screen.getByText('FINAL OUTPUT / 最终输出')).toBeInTheDocument()
    expect(screen.getByText('这是最终交付摘要')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'loop #21' })).toHaveAttribute('href', '/tasks/11#loop-21')
    expect(screen.getByRole('link', { name: '会话 latest-l →' })).toHaveAttribute('href', '/threads/latest-loop-thread')
    expect(container.querySelector('a[href="/tasks/11"]')).not.toBeNull()
    expect(screen.getByRole('link', { name: 'Agent 定义：worker →' })).toHaveAttribute('href', '/agents/worker')
    expect(container.querySelector('a[href="/threads/legacy-task-thread"]')).toBeNull()
  })

  it('交付验收是 task 内动作区，允许已识别的人或 Agent 验收', async () => {
    const task = makeTask({ agent_name: 'worker', status: 'success', pool_status: 'review' })
    apiMock.allTasks.mockResolvedValue([task])
    apiMock.taskDetail.mockResolvedValue(makeTaskDetail({ task, loops: [makeLoop()] }))

    renderPage('/', '/', <Home />)

    expect(await screen.findByText('交付验收 · 1 份交付')).toBeInTheDocument()
    expect(screen.getByText('任何已识别的人或 Agent 均可验收')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '采纳这份' })).toBeInTheDocument()
    expect(screen.queryByText(/验收台/)).toBeNull()
  })

  it('首页未指派时创建任务实例', async () => {
    const user = userEvent.setup()
    renderPage('/', '/', <Home />)

    await user.type(screen.getByPlaceholderText(/输入任务指令/), '创建一个开放任务')
    await user.click(screen.getByRole('button', { name: '放入任务池' }))

    await waitFor(() => expect(apiMock.createTask).toHaveBeenCalledWith('创建一个开放任务', undefined, undefined, 'auto'))
    expect(apiMock.agentDispatch).not.toHaveBeenCalled()
  })

  it('首页指派时仍调用原有 dispatch 行为', async () => {
    const user = userEvent.setup()
    apiMock.agents.mockResolvedValue([makeAgent()])
    renderPage('/', '/', <Home />)

    const selector = await screen.findByRole('combobox', { name: '执行 Agent' })
    await user.selectOptions(selector, 'worker')
    await user.type(screen.getByPlaceholderText(/输入任务指令/), '执行指定任务')
    await user.click(screen.getByRole('button', { name: '指派并发起' }))

    await waitFor(() => expect(apiMock.agentDispatch).toHaveBeenCalledWith('worker', '执行指定任务', undefined, 'auto'))
    expect(apiMock.createTask).not.toHaveBeenCalled()
  })

  it('Agent 详情返回 Registry，定义与关联任务实例分区且无 thread 直达', async () => {
    const user = userEvent.setup()
    apiMock.agentTasks.mockResolvedValue([
      makeTask({ agent_name: 'worker', thread_id: 'legacy-task-thread' }),
    ])
    const { container } = renderPage('/agents/worker', '/agents/:name', <AgentDetailPage />)

    await screen.findByText('整理发布简报')
    expect(screen.getByRole('link', { name: '← AGENT REGISTRY' })).toHaveAttribute('href', '/registry')
    expect(screen.getByText(/AGENT DEFINITION/)).toBeInTheDocument()
    expect(screen.getByText(/关联任务实例/)).toBeInTheDocument()
    expect(container.querySelector('a[href="/tasks/11"]')).not.toBeNull()
    expect(container.querySelector('a[href="/threads/legacy-task-thread"]')).toBeNull()

    await user.type(screen.getByPlaceholderText(/输入指令发起任务实例/), '新任务')
    await user.click(screen.getByRole('button', { name: '派发' }))
    await waitFor(() => expect(apiMock.agentDispatch).toHaveBeenCalledWith('worker', '新任务'))
  })

  it('系统 Fleet 操作 Agent 配置只读且没有生命周期入口', async () => {
    apiMock.agent.mockResolvedValue({
      registry: makeAgent({ name: 'fleet-ops-01', display_name: 'Fleet 操作助手' }),
      yaml: { kind: 'Agent', metadata: { name: 'fleet-ops-01' } },
    })
    renderPage('/agents/fleet-ops-01', '/agents/:name', <AgentDetailPage />)

    expect(await screen.findByText('系统内置 · 配置只读')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '保存并部署' })).toBeNull()
    expect(screen.queryByRole('button', { name: '停止' })).toBeNull()
    expect(screen.queryByRole('button', { name: '拉起' })).toBeNull()
    expect(screen.queryByRole('button', { name: '销毁' })).toBeNull()
    expect(document.querySelector('textarea.lcd')).toHaveAttribute('readonly')
  })

  it('Task 详情区分 Agent 定义、Agent Loop、人工 Loop，并支持 loop 锚点', async () => {
    const task = makeTask({ agent_name: 'worker', parent_task_id: 9, pool_status: 'review' })
    const humanLoop = makeLoop({
      id: 22,
      claimant_type: 'human',
      claimant: 'me',
      thread_id: null,
      deliverable: null,
      latest_output: null,
    })
    apiMock.taskDetail.mockResolvedValue(makeTaskDetail({
      task,
      loops: [makeLoop(), humanLoop],
    }))

    renderPage('/tasks/11#loop-22', '/tasks/:tid', <TaskDetailPage />)

    const definitionLink = await screen.findByRole('link', { name: 'Agent 定义：worker →' })
    expect(definitionLink).toHaveAttribute('href', '/agents/worker')
    expect(screen.getByRole('link', { name: /TASK\s*#9/ })).toHaveAttribute('href', '/tasks/9')
    expect(screen.getByRole('link', { name: '会话 loop-thr →' })).toHaveAttribute('href', '/threads/loop-thread')

    const humanCard = screen.getByText('loop #22').closest('#loop-22')
    expect(humanCard).not.toBeNull()
    expect(within(humanCard as HTMLElement).queryByRole('link', { name: /会话/ })).toBeNull()
    await waitFor(() => expect(window.HTMLElement.prototype.scrollIntoView).toHaveBeenCalled())
  })

  it('失败 Task 显示子任务与恢复现场，并区分断点续跑和重新执行', async () => {
    const user = userEvent.setup()
    const failed = makeLoop({
      status: 'claimed',
      deliverable: null,
      latest_output: null,
      error: 'Recursion limit of 100 reached',
      failure_kind: 'recursion',
      continuation_count: 1,
      last_checkpoint_id: 'checkpoint-1',
    })
    apiMock.taskDetail.mockResolvedValue(makeTaskDetail({
      loops: [failed],
      children: [makeTask({ id: 31, parent_task_id: 11, agent_name: 'worker', input: '已完成创建的子任务' })],
    }))
    renderPage('/tasks/11', '/tasks/:tid', <TaskDetailPage />)

    expect(await screen.findByText('CHILD TASKS / 已创建的子任务')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'TASK #31' })).toHaveAttribute('href', '/tasks/31')
    expect(screen.getByText('异常：运行步数耗尽')).toBeInTheDocument()
    expect(screen.getByText('断点续跑 1/3')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '从断点继续' }))
    await waitFor(() => expect(apiMock.loopResume).toHaveBeenCalledWith(21))
    expect(screen.getByRole('button', { name: '重新执行' })).toBeInTheDocument()
  })

  it('Thread 详情保留实例 breadcrumb，Agent 定义为独立横向链接', async () => {
    apiMock.reviewContext.mockResolvedValue({
      loop: makeLoop(),
      task: { id: 11, pool_status: 'review', input: '整理发布简报' },
    })
    renderPage('/threads/loop-thread', '/threads/:tid', <ThreadDetailPage />)

    expect(await screen.findByRole('link', { name: /TASK\s*#11/ })).toHaveAttribute('href', '/tasks/11')
    expect(screen.getByRole('link', { name: /LOOP\s*#21/ })).toHaveAttribute('href', '/tasks/11#loop-21')
    expect(screen.getByText('THREAD')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Agent 定义：worker →' })).toHaveAttribute('href', '/agents/worker')
  })

  it('自由 Thread 没有任务上下文时不显示空实例 breadcrumb', async () => {
    renderPage('/threads/free-thread', '/threads/:tid', <ThreadDetailPage />)

    expect(await screen.findByText('Agent 会话实例详情')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /^TASK/ })).toBeNull()
    expect(screen.queryByRole('link', { name: /^LOOP/ })).toBeNull()
  })

  it('Threads 列表分别链接会话实例与 Agent 定义', async () => {
    const row: FleetRow = {
      thread_id: 'loop-thread',
      display_name: '整理发布简报',
      assistant_id: 'worker',
      thread_status: 'success',
      created_at: '2026-10-27T06:00:00Z',
      updated_at: '2026-10-27T06:02:00Z',
      run_count: 1,
      total_tokens: 12,
      total_input_tokens: 8,
      total_output_tokens: 4,
      llm_call_count: 1,
      last_run_status: 'success',
      last_run_at: '2026-10-27T06:02:00Z',
      first_message: '整理发布简报',
      checkpoint_count: 2,
    }
    apiMock.fleet.mockResolvedValue([row])
    renderPage('/threads', '/threads', <ThreadsPage />)

    expect(await screen.findByRole('link', { name: 'Agent 定义：worker →' })).toHaveAttribute('href', '/agents/worker')
    expect(screen.getByText('Thread 实例观测')).toBeInTheDocument()
    expect(document.querySelector('a[href="/threads/loop-thread"]')).not.toBeNull()
  })
})
