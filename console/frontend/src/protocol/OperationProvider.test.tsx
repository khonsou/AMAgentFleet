import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { OperationProvider } from './OperationProvider'
import { useOperations } from './useOperations'

const mocks = vi.hoisted(() => ({ executeOperation: vi.fn(), getProtocol: vi.fn(), operationKey: vi.fn(() => 'gui-fixed') }))
vi.mock('./client', () => mocks)

function WriteButton({ operation = 'tasks.close', label = '关闭 Task' }: { operation?: string; label?: string }) {
  const { execute } = useOperations()
  return <button onClick={() => void execute(operation, { path_params: { task_id: 1 } })}>{label}</button>
}

describe('共享 Protocol Provider', () => {
  beforeEach(() => { vi.clearAllMocks() })
  afterEach(cleanup)

  it('挂载时不预取协议，保持旧页面请求不变', () => {
    render(<OperationProvider><div>Legacy Console</div></OperationProvider>)
    expect(screen.getByText('Legacy Console')).toBeInTheDocument()
    expect(mocks.getProtocol).not.toHaveBeenCalled()
  })

  it('普通写操作不弹确认框，仍携带确认与幂等键', async () => {
    mocks.getProtocol.mockResolvedValue({ operations: [{ id: 'tasks.create', method: 'POST', path: '/api/tasks', surface: 'control-plane', write: true, description: '创建任务' }] })
    mocks.executeOperation.mockResolvedValue({ operation_id: 'tasks.create', status: 201, result: { task_id: 1 }, truncated: false })
    const user = userEvent.setup()
    render(<OperationProvider><WriteButton operation="tasks.create" label="创建 Task" /></OperationProvider>)
    await user.click(screen.getByText('创建 Task'))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    await waitFor(() => expect(mocks.executeOperation).toHaveBeenCalledWith('tasks.create', expect.objectContaining({ confirmed: true, idempotency_key: 'gui-fixed' })))
  })

  it('高风险操作确认前不发送，确认后立即关闭且后台继续执行', async () => {
    mocks.getProtocol.mockResolvedValue({ operations: [{ id: 'tasks.close', method: 'POST', path: '/api/tasks/{task_id}/close', surface: 'control-plane', write: true, confirmation: 'high_risk', description: '关闭任务' }] })
    let finish: (value: unknown) => void = () => {}
    mocks.executeOperation.mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const user = userEvent.setup()
    render(<OperationProvider><WriteButton /></OperationProvider>)
    await user.click(screen.getByText('关闭 Task'))
    expect(await screen.findByRole('alertdialog')).toBeInTheDocument()
    expect(mocks.executeOperation).not.toHaveBeenCalled()
    await user.click(screen.getByText('确认执行'))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(mocks.executeOperation).toHaveBeenCalledWith('tasks.close', expect.objectContaining({ confirmed: true, idempotency_key: 'gui-fixed' }))
    await act(async () => {
      finish({ operation_id: 'tasks.close', status: 200, result: {}, truncated: false })
      await Promise.resolve()
    })
  })
})
