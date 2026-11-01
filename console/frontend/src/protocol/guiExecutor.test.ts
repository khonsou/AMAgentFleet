import { afterEach, describe, expect, it, vi } from 'vitest'
import { executeGuiOperation, registerOperationExecutor } from './guiExecutor'

describe('旧 GUI 写执行桥', () => {
  afterEach(() => { vi.restoreAllMocks() })

  it('Provider 未就绪时明确拒绝，不绕过协议直连', async () => {
    await expect(executeGuiOperation('tasks.create')).rejects.toThrow('Fleet 操作客户端尚未就绪')
  })

  it('注册后只委托给当前 Provider，并在卸载后失效', async () => {
    const execute = vi.fn().mockResolvedValue({ operation_id: 'tasks.create', status: 200, result: { task_id: 1 }, truncated: false })
    const unregister = registerOperationExecutor(execute)
    await expect(executeGuiOperation('tasks.create', { body: { input: 'x' } })).resolves.toMatchObject({ status: 200 })
    expect(execute).toHaveBeenCalledWith('tasks.create', { body: { input: 'x' } })
    unregister()
    await expect(executeGuiOperation('tasks.create')).rejects.toThrow('Fleet 操作客户端尚未就绪')
  })
})
