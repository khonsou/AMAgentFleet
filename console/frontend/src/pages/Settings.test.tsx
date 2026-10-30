import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SettingsPage from './Settings'

const apiMock = vi.hoisted(() => ({
  pats: vi.fn(),
  patCreate: vi.fn(),
  patRevoke: vi.fn(),
  opsCredential: vi.fn(),
  opsCredentialRotate: vi.fn(),
  managedModels: vi.fn(),
  managedModelSave: vi.fn(),
  managedModelTest: vi.fn(),
}))

vi.mock('@/lib/api', () => ({ api: apiMock }))

const status = {
  agent: 'fleet-ops-01',
  system: true as const,
  configured: true,
  pat_id: 'fap-system-fleet-ops-01',
  scopes: ['read', 'write'],
  version: 3,
  created_at: '2026-10-28T01:00:00Z',
  rotated_at: '2026-10-28T02:00:00Z',
  last_used_at: null,
}

beforeEach(() => {
  vi.clearAllMocks()
  apiMock.pats.mockResolvedValue([
    {
      id: status.pat_id,
      name: 'Fleet 操作助手（系统内置）',
      scopes: ['read', 'write'],
      system: true,
      prefix: 'dfp_',
      expires_at: null,
      revoked_at: null,
      last_used_at: null,
      created_at: status.created_at,
    },
  ])
  apiMock.opsCredential.mockResolvedValueOnce(status).mockResolvedValue({ ...status, version: 4 })
  apiMock.opsCredentialRotate.mockResolvedValue({ ...status, version: 4 })
  apiMock.managedModels.mockResolvedValue({ models: [] })
  apiMock.managedModelSave.mockResolvedValue({})
  apiMock.managedModelTest.mockResolvedValue({ ok: true, message: 'ok' })
  vi.stubGlobal('confirm', vi.fn(() => true))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('系统 Fleet 操作凭证设置', () => {
  it('系统 PAT 固定读写权限且没有吊销入口', async () => {
    render(<SettingsPage />)

    expect(await screen.findByText('Fleet 操作助手 · fleet-ops-01')).toBeInTheDocument()
    expect(screen.getByText(/固定权限 read\+write/)).toBeInTheDocument()
    expect(screen.getAllByText('系统内置').length).toBeGreaterThan(0)
    expect(screen.getByText('仅可轮换')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '吊销' })).toBeNull()
  })

  it('人工轮换需要确认且不回显 token', async () => {
    const user = userEvent.setup()
    render(<SettingsPage />)

    await user.click(await screen.findByRole('button', { name: '人工轮换系统 PAT' }))
    await waitFor(() => expect(apiMock.opsCredentialRotate).toHaveBeenCalledTimes(1))
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('旧 token 将立即失效'))
    expect(await screen.findByText(/凭证版本 v4/)).toBeInTheDocument()
    expect(screen.queryByText(/dfp_[0-9A-Za-z]/)).toBeNull()
  })
})
