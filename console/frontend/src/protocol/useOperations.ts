import { useContext } from 'react'
import { OperationContext } from './context'

export function useOperations() {
  const value = useContext(OperationContext)
  if (!value) throw new Error('useOperations must be used within OperationProvider')
  return value
}
