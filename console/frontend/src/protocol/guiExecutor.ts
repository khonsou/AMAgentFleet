import type { OperationRequest, OperationResult } from './client'

type OperationExecutor = <T = unknown>(operationId: string, request?: OperationRequest) => Promise<OperationResult<T>>

let currentExecutor: OperationExecutor | null = null

/** The single App-level OperationProvider registers the GUI write bridge. */
export function registerOperationExecutor(executor: OperationExecutor): () => void {
  currentExecutor = executor
  return () => {
    if (currentExecutor === executor) currentExecutor = null
  }
}

/** Legacy Console API methods keep their signatures while writes share Provider confirmation. */
export function executeGuiOperation<T = unknown>(operationId: string, request: OperationRequest = {}): Promise<OperationResult<T>> {
  if (!currentExecutor) return Promise.reject(new Error('Fleet 操作客户端尚未就绪，请稍后重试'))
  return currentExecutor<T>(operationId, request)
}
