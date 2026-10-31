import { useMemo, useState } from 'react'
import { useOperations } from '../protocol/useOperations'
import { downloadArtifact, type OperationDefinition } from '../protocol/client'

function paramsFor(operation: OperationDefinition) {
  const path = Object.fromEntries([...operation.path.matchAll(/\{([^}]+)\}/g)].map((match) => [match[1], match[1].endsWith('id') || match[1].endsWith('_id') ? 0 : '']))
  return { path, query: operation.query || {}, body: operation.body || null }
}

export default function OperationsCenter({ onClose }: { onClose: () => void }) {
  const { protocol, execute, busy } = useOperations()
  const [filter, setFilter] = useState('')
  const [selected, setSelected] = useState<OperationDefinition | null>(null)
  const [pathText, setPathText] = useState('{}')
  const [queryText, setQueryText] = useState('{}')
  const [bodyText, setBodyText] = useState('null')
  const [result, setResult] = useState<unknown>(null)
  const [error, setError] = useState('')

  const operations = useMemo(() => (protocol?.operations || []).filter((operation) => `${operation.id} ${operation.description}`.toLowerCase().includes(filter.toLowerCase())), [protocol, filter])
  const select = (operation: OperationDefinition) => {
    const params = paramsFor(operation)
    setSelected(operation)
    setPathText(JSON.stringify(params.path, null, 2))
    setQueryText(JSON.stringify(params.query, null, 2))
    setBodyText(JSON.stringify(params.body, null, 2))
    setResult(null)
    setError('')
  }
  const run = async () => {
    if (!selected) return
    try {
      if (selected.id === 'threads.artifact') {
        await downloadArtifact(JSON.parse(pathText))
        setResult({ status: 'downloaded', operation_id: selected.id })
      } else {
        const response = await execute(selected.id, { path_params: JSON.parse(pathText), query: JSON.parse(queryText), body: JSON.parse(bodyText) })
        setResult(response)
      }
      setError('')
    } catch (reason) { setError(String(reason)) }
  }

  return <>
    <div className="drawer-top"><span className="drawer-kicker">FLEET OPERATIONS PROTOCOL</span><button className="drawer-close" onClick={onClose} aria-label="关闭操作中心">×</button></div>
    <h2>Operations Center</h2>
    <p className="drawer-subtitle">Console、Agent 与 Playground 共用同一份 operation 清单。写操作会先展示参数并要求明确确认。</p>
    {!selected ? <>
      <input className="operation-search" aria-label="搜索操作" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="搜索 tasks、agents、models…" />
      <div className="operation-grid">{operations.map((operation) => <button key={operation.id} className="operation-card" onClick={() => select(operation)}><b>{operation.id}</b><small>{operation.description}</small><small>{operation.confirmation === 'human_only' ? 'HUMAN ONLY' : operation.write ? 'WRITE / CONFIRM' : 'READ'}</small></button>)}</div>
    </> : <>
      <button className="text-action" onClick={() => setSelected(null)}>← ALL OPERATIONS</button>
      <section className="drawer-section"><h3>{selected.id} / {selected.surface}</h3><p className="drawer-subtitle">{selected.description}</p></section>
      <div className="operation-form">
        <label>PATH PARAMS / JSON<textarea value={pathText} onChange={(event) => setPathText(event.target.value)} /></label>
        <label>QUERY / JSON<textarea value={queryText} onChange={(event) => setQueryText(event.target.value)} /></label>
        <label>BODY / JSON<textarea value={bodyText} onChange={(event) => setBodyText(event.target.value)} /></label>
        <button className="drawer-action" onClick={() => void run()} disabled={busy}>{selected.id === 'threads.artifact' ? '下载产物' : selected.write ? '检查参数并确认执行' : '执行读取'}</button>
      </div>
      {error && <p role="alert" className="operation-error">{error}</p>}
      {result !== null && <details className="drawer-section" open={selected.confirmation === 'human_only'}><summary className="drawer-kicker">OPERATION RESULT</summary><pre className={`operation-json ${selected.confirmation === 'human_only' ? 'sensitive-result' : ''}`}>{JSON.stringify(result, null, 2)}</pre></details>}
    </>}
  </>
}
