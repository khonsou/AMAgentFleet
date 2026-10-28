import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import FleetOpsWidget from './FleetOpsWidget'

const apiMock = vi.hoisted(() => ({
  create: vi.fn(),
  createTask: vi.fn(),
  messages: vi.fn(),
  send: vi.fn(),
  thread: vi.fn(),
}))
vi.mock('@/lib/api', () => ({ api: apiMock }))

function renderWidget() {
  return render(<MemoryRouter><FleetOpsWidget /></MemoryRouter>)
}

async function openWidget(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: '打开舰队操作助手' }))
  await screen.findByRole('button', { name: '发送' })
}

beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.clear()
  apiMock.create.mockResolvedValue({ thread_id: 'ops-thread-1' })
  apiMock.messages.mockResolvedValue([])
  apiMock.send.mockResolvedValue({})
  apiMock.thread.mockResolvedValue({ thread: { assistant_id: 'fleet-ops-01' }, runs: [], checkpoints: 1 })
})
afterEach(cleanup)

describe('FleetOpsWidget', () => {
  it('打开时创建 fleet-ops-01 的空白持久会话，关闭不销毁会话', async () => {
    const user = userEvent.setup()
    renderWidget()
    await openWidget(user)
    await waitFor(() => expect(apiMock.create).toHaveBeenCalledWith(undefined, 'fleet-ops-01'))
    expect(screen.getByLabelText('给舰队操作助手的指令')).toBeEnabled()
    expect(screen.getByText('THREAD ops-thread-1')).toBeInTheDocument()
    expect(window.localStorage.getItem('fleet-ops-thread-id')).toBe('ops-thread-1')
    expect(apiMock.createTask).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: '关闭操作助手' }))
    expect(screen.queryByRole('region', { name: '舰队操作助手' })).toBeNull()
    await openWidget(user)
    expect(apiMock.create).toHaveBeenCalledTimes(1)
  })

  it('恢复已保存会话及事件信封中的历史消息，不新建 Thread', async () => {
    const user = userEvent.setup()
    window.localStorage.setItem('fleet-ops-thread-id', 'saved-thread')
    apiMock.messages.mockResolvedValue([
      { event_type: 'llm.human.input', content: { type: 'human', content: '查询待验收任务' } },
      { event_type: 'llm.ai.response', content: { type: 'ai', content: [{ type: 'text', text: '当前没有待验收任务。' }] } },
      { event_type: 'tool.call', content: { type: 'tool', content: '内部工具结果' } },
    ])
    renderWidget()
    await openWidget(user)
    expect(await screen.findByText('查询待验收任务')).toBeInTheDocument()
    expect(screen.getByText('当前没有待验收任务。')).toBeInTheDocument()
    expect(screen.queryByText('内部工具结果')).toBeNull()
    expect(apiMock.create).not.toHaveBeenCalled()
    expect(screen.getByRole('link', { name: '打开完整会话' })).toHaveAttribute('href', '/threads/saved-thread')
  })

  it('将 ask_clarification 工具消息作为可见追问，不再静默吞掉', async () => {
    const user = userEvent.setup()
    window.localStorage.setItem('fleet-ops-thread-id', 'saved-thread')
    apiMock.messages.mockResolvedValue([
      { content: { type: 'human', content: '新建一个待分配任务' } },
      { type: 'tool', name: 'ask_clarification', content: '❓ 请提供任务正文（input）。' },
    ])
    renderWidget()
    await openWidget(user)
    expect(await screen.findByText('❓ 请提供任务正文（input）。')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('执行结束但没有任何可见回答时显示明确错误', async () => {
    const user = userEvent.setup()
    apiMock.messages.mockResolvedValue([{ content: { type: 'human', content: '新建任务' } }])
    renderWidget()
    await openWidget(user)
    await user.type(screen.getByLabelText('给舰队操作助手的指令'), '新建任务')
    await user.click(screen.getByRole('button', { name: '发送' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('没有返回可展示的结果')
  })

  it('发送自然语言指令后刷新同一会话，普通聊天不调用任务创建', async () => {
    const user = userEvent.setup()
    apiMock.messages.mockResolvedValue([
      { content: { type: 'human', content: '你好' } },
      { content: { type: 'ai', content: '你好，我可以帮你操作舰队。' } },
    ])
    renderWidget()
    await openWidget(user)
    await user.type(screen.getByLabelText('给舰队操作助手的指令'), '你好')
    await user.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(apiMock.send).toHaveBeenCalledWith('ops-thread-1', '你好', 'fleet-ops-01'))
    expect(await screen.findByText('你好，我可以帮你操作舰队。')).toBeInTheDocument()
    expect(apiMock.createTask).not.toHaveBeenCalled()
    expect(screen.getByLabelText('给舰队操作助手的指令')).toHaveValue('')
  })

  it('恢复失败后保留可读错误并允许重新连接', async () => {
    const user = userEvent.setup()
    apiMock.create.mockRejectedValueOnce(new Error('500: unavailable')).mockResolvedValueOnce({ thread_id: 'recovered' })
    renderWidget()
    await openWidget(user)
    expect(await screen.findByRole('alert')).toHaveTextContent('500: unavailable')
    await user.click(screen.getByRole('button', { name: '重新连接' }))
    await waitFor(() => expect(screen.getByText('THREAD recovered')).toBeInTheDocument())
    expect(apiMock.create).toHaveBeenCalledTimes(2)
  })

  it('历史 Thread 不存在时清理本地引用，不继续向失效实例发送', async () => {
    const user = userEvent.setup()
    window.localStorage.setItem('fleet-ops-thread-id', 'deleted-thread')
    apiMock.messages.mockRejectedValue(new Error('404: not found'))
    renderWidget()
    await openWidget(user)
    expect(await screen.findByRole('alert')).toHaveTextContent('404: not found')
    expect(window.localStorage.getItem('fleet-ops-thread-id')).toBeNull()
    expect(screen.getByLabelText('给舰队操作助手的指令')).toBeDisabled()
    expect(apiMock.send).not.toHaveBeenCalled()
  })

  it('旧浮窗 Thread 身份为空或错误时新建会话，不恢复错误身份', async () => {
    const user = userEvent.setup()
    window.localStorage.setItem('fleet-ops-thread-id', 'legacy-lead-thread')
    apiMock.thread.mockResolvedValue({ thread: { assistant_id: null }, runs: [], checkpoints: 1 })
    renderWidget()
    await openWidget(user)
    expect(await screen.findByText('THREAD ops-thread-1')).toBeInTheDocument()
    expect(apiMock.create).toHaveBeenCalledWith(undefined, 'fleet-ops-01')
    expect(apiMock.messages).not.toHaveBeenCalled()
    expect(window.localStorage.getItem('fleet-ops-thread-id')).toBe('ops-thread-1')
  })
})
