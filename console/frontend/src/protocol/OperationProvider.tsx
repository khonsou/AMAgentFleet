import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { executeOperation, getProtocol, operationKey, type OperationDefinition, type OperationRequest, type OperationResult, type ProtocolDocument } from './client'
import { OperationContext } from './context'
import { registerOperationExecutor } from './guiExecutor'
import './operations.css'

interface PendingOperation {
  definition: OperationDefinition
  request: OperationRequest
  resolve: (result: OperationResult) => void
  reject: (error: Error) => void
}

const BACKGROUND_OPERATIONS = new Set(['threads.create', 'threads.send', 'runs.wait', 'runs.stream'])

export function OperationProvider({ children }: { children: ReactNode }) {
  const [protocol, setProtocol] = useState<ProtocolDocument | null>(null)
  const [pending, setPending] = useState<PendingOperation | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const perform = useCallback(async <T,>(definition: OperationDefinition, request: OperationRequest): Promise<OperationResult<T>> => {
    const trackBusy = !BACKGROUND_OPERATIONS.has(definition.id)
    if (trackBusy) setBusy(true)
    setError('')
    try {
      const prepared = definition.write
        ? { ...request, confirmed: true, idempotency_key: request.idempotency_key || operationKey(definition.id) }
        : request
      return await executeOperation<T>(definition.id, prepared)
    } catch (reason) {
      setError(String(reason))
      throw reason
    } finally { if (trackBusy) setBusy(false) }
  }, [])

  const execute = useCallback(async <T,>(operationId: string, request: OperationRequest = {}): Promise<OperationResult<T>> => {
    // 全局 Provider 不在旧页面挂载时预取协议，首次 operation 才加载清单。
    const document = protocol || await getProtocol()
    if (!protocol) setProtocol(document)
    const definition = document.operations.find((item) => item.id === operationId)
    if (!definition) throw new Error(`未登记的 Fleet 操作：${operationId}`)
    const requiresConfirmation = definition.write && ['high_risk', 'human_only'].includes(definition.confirmation || '')
    if (requiresConfirmation && !request.confirmed) {
      return new Promise<OperationResult<T>>((resolve, reject) => {
        setPending({ definition, request, resolve: resolve as (result: OperationResult) => void, reject })
      })
    }
    return perform<T>(definition, request)
  }, [perform, protocol])

  useEffect(() => registerOperationExecutor(execute), [execute])

  const value = useMemo(() => ({ protocol, busy, execute }), [protocol, busy, execute])
  const close = () => { if (!busy) { pending?.reject(new Error('操作已取消')); setPending(null) } }
  const confirm = () => {
    if (!pending) return
    const operation = pending
    // 确认的是命令发送，不是等待 Agent 执行结束；弹窗立即退出，结果在后台返回。
    setPending(null)
    void perform(operation.definition, operation.request).then(operation.resolve).catch(operation.reject)
  }

  return <OperationContext.Provider value={value}>
    {children}
    {pending && <div className="operation-confirm-backdrop">
      <section className="operation-confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="operation-confirm-title">
        <div className="operation-confirm-kicker">HIGH RISK OPERATION / CONFIRM</div>
        <h2 id="operation-confirm-title">确认执行 {pending.definition.id}</h2>
        <p className="operation-confirm-subtitle">{pending.definition.description}</p>
        <div className="operation-confirm-summary">
          <p>这是高风险操作。确认只代表同意发送命令，后台执行结果会另行返回。</p>
          {pending.definition.confirmation === 'human_only' && <p>此操作只能由人类在 GUI 中完成，秘密不会进入 Agent Thread。</p>}
        </div>
        {error && <p role="alert" className="operation-confirm-error">{error}</p>}
        <div className="operation-confirm-actions">
          <button className="operation-confirm-action secondary" onClick={close} disabled={busy}>取消</button>
          <button className="operation-confirm-action" onClick={confirm} disabled={busy}>{busy ? '执行中…' : '确认执行'}</button>
        </div>
      </section>
    </div>}
  </OperationContext.Provider>
}
