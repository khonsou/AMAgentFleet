import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ModelSettings from './ModelSettings'

const apiMock = vi.hoisted(() => ({
  managedModels: vi.fn(),
  managedModelSave: vi.fn(),
  managedModelTest: vi.fn(),
}))
vi.mock('@/lib/api', () => ({ api: apiMock }))

const managed = {
  name: 'reasoner', display_name: 'Reasoner', model: 'deepseek-reasoner',
  base_url: 'https://api.deepseek.com', enabled: true, supports_vision: false,
  context_window: null, max_tokens: null, revision: 'rev-1', source: 'managed', has_api_key: true,
}

beforeEach(() => {
  vi.clearAllMocks()
  apiMock.managedModels.mockResolvedValue({ models: [managed] })
  apiMock.managedModelSave.mockResolvedValue(managed)
  apiMock.managedModelTest.mockResolvedValue({ ok: true, message: 'ok' })
})
afterEach(cleanup)

describe('Managed Models 密钥边界', () => {
  it('目录不回显 Key，编辑留空保存不发送 api_key', async () => {
    const user = userEvent.setup()
    render(<ModelSettings />)
    await user.click(await screen.findByRole('button', { name: '编辑' }))
    expect(screen.getByLabelText('API Key')).toHaveValue('')

    await user.click(screen.getByRole('button', { name: '保存修改' }))
    await waitFor(() => expect(apiMock.managedModelSave).toHaveBeenCalledTimes(1))
    const [config, revision] = apiMock.managedModelSave.mock.calls[0]
    expect(revision).toBe('rev-1')
    expect(config).not.toHaveProperty('api_key')
    expect(config.name).toBe('reasoner')
  })

  it('新建与测试只发送输入的 Key，不把响应秘密写进页面', async () => {
    const user = userEvent.setup()
    render(<ModelSettings />)
    await user.type(await screen.findByLabelText('Profile 名称'), 'qwen')
    await user.type(screen.getByLabelText('Provider 模型'), 'qwen3-max')
    await user.type(screen.getByLabelText('OpenAI 兼容地址'), 'https://dashscope.aliyuncs.com/compatible-mode/v1')
    await user.type(screen.getByLabelText('API Key'), 'secret-once')
    await user.click(screen.getByRole('button', { name: '测试连接' }))
    await waitFor(() => expect(apiMock.managedModelTest).toHaveBeenCalledTimes(1))
    expect(apiMock.managedModelTest.mock.calls[0][0].api_key).toBe('secret-once')
    expect(screen.queryByDisplayValue('secret-once')).toBeInTheDocument()
    expect(screen.queryByText('secret-once')).toBeNull()
  })
})
