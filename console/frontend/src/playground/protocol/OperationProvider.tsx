import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { executeOperation, getProtocol, operationKey, type OperationDefinition, type OperationRequest, type OperationResult, type ProtocolDocument } from './client'
import { OperationContext } from './context'

interface PendingOperation {
  definition: OperationDefinition
  request: OperationRequest
  resolve: (result: OperationResult) => void
  reject: (error: Error) => void
}

export function OperationProvider({ children }: { children: ReactNode }) {
  const [protocol, setProtocol] = useState<ProtocolDocument | null>(null)
  const [pending, setPending] = useState<PendingOperation | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => { getProtocol().then(setProtocol).catch((reason) => setError(String(reason))) }, [])

  const perform = useCallback(async <T,>(definition: OperationDefinition, request: OperationRequest): Promise<OperationResult<T>> => {
    setBusy(true)
    setError('')
    try {
      const prepared = definition.write
        ? { ...request, confirmed: true, idempotency_key: request.idempotency_key || operationKey(definition.id) }
        : request
      return await executeOperation<T>(definition.id, prepared)
    } catch (reason) {
      setError(String(reason))
      throw reason
    } finally { setBusy(false) }
  }, [])

  const execute = useCallback(async <T,>(operationId: string, request: OperationRequest = {}): Promise<OperationResult<T>> => {
    const document = protocol || await getProtocol()
    if (!protocol) setProtocol(document)
    const definition = document.operations.find((item) => item.id === operationId)
    if (!definition) throw new Error(`未登记的 Fleet 操作：${operationId}`)
    if (definition.write && !request.confirmed) {
      return new Promise<OperationResult<T>>((resolve, reject) => {
        setPending({ definition, request, resolve: resolve as (result: OperationResult) => void, reject })
      })
    }
    return perform<T>(definition, request)
  }, [perform, protocol])

  const value = useMemo(() => ({ protocol, busy, execute }), [protocol, busy, execute])
  const close = () => { if (!busy) { pending?.reject(new Error('操作已取消')); setPending(null) } }
  const confirm = async () => {
    if (!pending) return
    try {
      const result = await perform(pending.definition, pending.request)
      pending.resolve(result)
      setPending(null)
    } catch { /* 保留确认框，允许修正参数或取消。 */ }
  }

  return <OperationContext.Provider value={value}>
    {children}
    {pending && <div className="drawer-backdrop open" style={{ zIndex: 40 }}>
      <section className="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="operation-confirm-title">
        <div className="drawer-kicker">WRITE OPERATION / CONFIRM</div>
        <h2 id="operation-confirm-title">确认执行 {pending.definition.id}</h2>
        <p className="drawer-subtitle">{pending.definition.description}</p>
        <div className="confirm-summary">
          <p>此操作会修改 Fleet 状态，请确认后继续。</p>
          {pending.definition.confirmation === 'human_only' && <p>此操作只能由人类在 GUI 中完成，秘密不会进入 Agent Thread。</p>}
        </div>
        {error && <p role="alert" className="operation-error">{error}</p>}
        <div className="confirm-actions">
          <button className="drawer-action secondary" onClick={close} disabled={busy}>取消</button>
          <button className="drawer-action" onClick={() => void confirm()} disabled={busy}>{busy ? '执行中…' : '确认执行'}</button>
        </div>
      </section>
    </div>}
  </OperationContext.Provider>
}
