import { createContext } from 'react'
import type { OperationRequest, OperationResult, ProtocolDocument } from './client'

export interface OperationContextValue {
  protocol: ProtocolDocument | null
  busy: boolean
  execute: <T = unknown>(operationId: string, request?: OperationRequest) => Promise<OperationResult<T>>
}

export const OperationContext = createContext<OperationContextValue | null>(null)
