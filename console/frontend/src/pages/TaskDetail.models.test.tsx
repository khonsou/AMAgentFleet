import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import TaskDetailPage from './TaskDetail'

const apiMock = vi.hoisted(() => ({ taskDetail: vi.fn() }))
vi.mock('@/lib/api', () => ({ api: apiMock }))
afterEach(cleanup)

describe('Task 实例模型快照', () => {
  it('显示 Task 继承策略、Loop 模型、选择者与来源', async () => {
    apiMock.taskDetail.mockResolvedValue({
      task: {
        id: 9, agent_name: 'worker-01', input: '多模型任务', source: 'web', status: 'running',
        pool_status: 'open', model_profile: 'reasoner', subagent_model_policy: 'inherit',
        parent_task_id: null, created_at: '2026-10-28T01:00:00Z',
      },
      comments: [],
      children: [],
      loops: [{
        id: 7, task_id: 9, claimant_type: 'agent', claimant: 'worker-01', thread_id: null,
        status: 'running', tokens: 12, version: 1, created_at: '2026-10-28T01:00:00Z',
        model_profile: 'reasoner', model_selected_by: 'task',
        model_snapshot: { profile: 'reasoner', model: 'deepseek-reasoner', display_name: '推理模型', source: 'task' },
      }],
    })
    render(<MemoryRouter initialEntries={['/tasks/9']}><Routes><Route path="/tasks/:tid" element={<TaskDetailPage />} /></Routes></MemoryRouter>)
    expect(await screen.findByText(/Subagent 策略：固定继承/)).toBeInTheDocument()
    expect(screen.getByText(/模型：推理模型/)).toHaveTextContent('模型：推理模型 · deepseek-reasoner · 选择者 task · 来源 task')
  })
})
