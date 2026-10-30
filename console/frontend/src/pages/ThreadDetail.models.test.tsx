import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ThreadDetailPage from './ThreadDetail'

const apiMock = vi.hoisted(() => ({ thread: vi.fn(), messages: vi.fn(), reviewContext: vi.fn() }))
vi.mock('@/lib/api', () => ({ api: apiMock }))
afterEach(cleanup)

describe('Thread 实际运行模型', () => {
  it('以 Run 记录的实际 model_name 为准', async () => {
    apiMock.thread.mockResolvedValue({
      thread: { thread_id: 'thread-1', agent_name: 'worker-01', status: 'success' }, checkpoints: 1,
      runs: [{
        run_id: 'run-12345678', status: 'success', model_name: 'deepseek-reasoner',
        created_at: '2026-10-28T01:00:00Z', total_tokens: 20, llm_call_count: 1, message_count: 2,
      }],
    })
    apiMock.messages.mockResolvedValue([])
    apiMock.reviewContext.mockResolvedValue({ loop: null })
    render(<MemoryRouter initialEntries={['/threads/thread-1']}><Routes><Route path="/threads/:tid" element={<ThreadDetailPage />} /></Routes></MemoryRouter>)
    expect(await screen.findByText('实际模型：deepseek-reasoner')).toBeInTheDocument()
  })
})
