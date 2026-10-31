import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { OperationProvider } from './OperationProvider'
import { useOperations } from './useOperations'

const mocks = vi.hoisted(() => ({ executeOperation: vi.fn(), getProtocol: vi.fn(), operationKey: vi.fn(() => 'gui-fixed') }))
vi.mock('./client', () => mocks)

function WriteButton() {
  const { execute } = useOperations()
  return <button onClick={() => void execute('tasks.close', { path_params: { task_id: 1 } })}>关闭 Task</button>
}

describe('统一写操作确认', () => {
  afterEach(cleanup)
  it('确认前不发送，确认后复用同一幂等键', async () => {
    mocks.getProtocol.mockResolvedValue({ operations: [{ id: 'tasks.close', method: 'POST', path: '/api/tasks/{task_id}/close', surface: 'control-plane', write: true, description: '关闭任务' }] })
    mocks.executeOperation.mockResolvedValue({ operation_id: 'tasks.close', status: 200, result: {}, truncated: false })
    const user = userEvent.setup()
    render(<OperationProvider><WriteButton /></OperationProvider>)
    await user.click(screen.getByText('关闭 Task'))
    expect(await screen.findByRole('alertdialog')).toBeInTheDocument()
    expect(mocks.executeOperation).not.toHaveBeenCalled()
    await user.click(screen.getByText('确认执行'))
    await waitFor(() => expect(mocks.executeOperation).toHaveBeenCalledWith('tasks.close', expect.objectContaining({ confirmed: true, idempotency_key: 'gui-fixed' })))
  })
})
