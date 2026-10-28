import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Home from './Home'

const apiMock = vi.hoisted(() => ({
  allTasks: vi.fn(), agents: vi.fn(), models: vi.fn(),
  createTask: vi.fn(), agentDispatch: vi.fn(),
}))
vi.mock('@/lib/api', () => ({ api: apiMock }))

beforeEach(() => {
  vi.clearAllMocks()
  apiMock.allTasks.mockResolvedValue([])
  apiMock.agents.mockResolvedValue([{ name: 'fleet-cmd-01', display_name: '舰队指挥官', status: 'registered' }])
  apiMock.models.mockResolvedValue([
    { name: 'deepseek-chat', model: 'deepseek-chat', display_name: 'DeepSeek Chat' },
    { name: 'reasoner', model: 'deepseek-reasoner', display_name: 'DeepSeek Reasoner' },
  ])
  apiMock.createTask.mockResolvedValue({ task_id: 1 })
  apiMock.agentDispatch.mockResolvedValue({ task_id: 2 })
})
afterEach(cleanup)

function renderHome() {
  return render(<MemoryRouter><Home /></MemoryRouter>)
}

describe('首页实例选模', () => {
  it('把 Profile 与固定继承策略随待分配 Task 提交', async () => {
    const user = userEvent.setup()
    renderHome()
    await user.selectOptions(await screen.findByLabelText('实例模型'), 'reasoner')
    await user.selectOptions(screen.getByLabelText('Subagent 模型策略'), 'inherit')
    await user.type(screen.getByPlaceholderText(/输入任务指令/), '测试多模型')
    await user.click(screen.getByRole('button', { name: '放入任务池' }))
    await waitFor(() => expect(apiMock.createTask).toHaveBeenCalledWith('测试多模型', undefined, 'reasoner', 'inherit'))
  })

  it('切回系统默认会重置继承，直接派发也携带选模', async () => {
    const user = userEvent.setup()
    renderHome()
    await user.selectOptions(await screen.findByLabelText('实例模型'), 'reasoner')
    await user.selectOptions(screen.getByLabelText('Subagent 模型策略'), 'inherit')
    await user.selectOptions(screen.getByLabelText('实例模型'), '')
    expect(screen.getByLabelText('Subagent 模型策略')).toHaveValue('auto')
    expect(screen.getByLabelText('Subagent 模型策略')).toBeDisabled()

    await user.selectOptions(screen.getByLabelText('执行 Agent'), 'fleet-cmd-01')
    await user.selectOptions(screen.getByLabelText('实例模型'), 'reasoner')
    await user.type(screen.getByPlaceholderText(/输入任务指令/), '测试派发')
    await user.click(screen.getByRole('button', { name: '指派并发起' }))
    await waitFor(() => expect(apiMock.agentDispatch).toHaveBeenCalledWith('fleet-cmd-01', '测试派发', 'reasoner', 'auto'))
  })
})
