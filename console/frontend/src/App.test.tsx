import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Link } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import App from './App'

const apiMock = vi.hoisted(() => ({
  create: vi.fn(),
  messages: vi.fn(),
  send: vi.fn(),
}))
vi.mock('@/lib/api', () => ({ api: apiMock }))
vi.mock('@/components/console/Rack', () => ({ default: () => null }))
vi.mock('@/components/console/AuthGate', () => ({
  default: ({ children }: { children: ReactNode }) => children,
}))
vi.mock('@/pages/Home', () => ({ default: () => <div>Fleet page</div> }))
vi.mock('@/pages/Registry', () => ({
  default: () => <><div>Registry page</div><Link to="/threads">前往 Threads</Link></>,
}))
vi.mock('@/pages/Threads', () => ({
  default: () => <><div>Threads page</div><Link to="/settings">前往 Settings</Link></>,
}))
vi.mock('@/pages/Settings', () => ({ default: () => <div>Settings page</div> }))
vi.mock('@/pages/ThreadDetail', () => ({ default: () => <div>Thread detail</div> }))
vi.mock('@/pages/AgentDetail', () => ({ default: () => <div>Agent detail</div> }))
vi.mock('@/pages/TaskDetail', () => ({ default: () => <div>Task detail</div> }))
vi.mock('@/pages/AuthCallback', () => ({ default: () => <div>OAuth callback</div> }))
vi.mock('@/playground/Playground', () => ({ default: () => <div>Playground page</div> }))

function renderAt(route: string) {
  return render(<MemoryRouter initialEntries={[route]}><App /></MemoryRouter>)
}

beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.clear()
  apiMock.create.mockResolvedValue({ thread_id: 'global-ops-thread' })
  apiMock.messages.mockResolvedValue([])
  apiMock.send.mockResolvedValue({})
})
afterEach(cleanup)

describe('全局舰队操作助手', () => {
  it.each([
    '/',
    '/registry',
    '/threads',
    '/settings',
    '/agents/fleet-cmd-01',
    '/tasks/191',
    '/threads/global-ops-thread',
  ])('在 %s 提供浮窗入口', (route) => {
    renderAt(route)
    expect(screen.getByRole('button', { name: '打开舰队操作助手' })).toBeInTheDocument()
  })

  it('Playground 使用自己的 Operations Center，不叠加旧浮窗', async () => {
    renderAt('/playground')
    expect(await screen.findByText('Playground page')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '打开舰队操作助手' })).toBeNull()
  })

  it('切换页面时保持浮窗展开状态和同一个持久 Thread', async () => {
    const user = userEvent.setup()
    renderAt('/registry')
    await user.click(screen.getByRole('button', { name: '打开舰队操作助手' }))
    expect(await screen.findByText('持久 Thread')).toBeInTheDocument()

    await user.click(screen.getByRole('link', { name: '前往 Threads' }))
    expect(screen.getByText('Threads page')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '舰队操作助手' })).toBeInTheDocument()

    await user.click(screen.getByRole('link', { name: '前往 Settings' }))
    expect(screen.getByText('Settings page')).toBeInTheDocument()
    expect(screen.getByText('持久 Thread')).toBeInTheDocument()
    await waitFor(() => expect(apiMock.create).toHaveBeenCalledTimes(1))
  })
})
