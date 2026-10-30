import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { api, type FleetRow } from '@/lib/api'
import { fmtTime, fmtTokens, fmtFullTime } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { Panel, PanelHead } from '@/components/console/Panel'
import { StatusPill } from '@/components/console/Status'
import { runStatusMeta } from '@/components/console/statusMeta'
import ErrorBanner from '@/components/console/ErrorBanner'

/** Thread 运行观测：快速定位活跃状态与异常，详细调用数据在 Thread 详情查看。 */
export default function ThreadsPage() {
  const [rows, setRows] = useState<FleetRow[]>([])
  const [err, setErr] = useState('')
  const [destroying, setDestroying] = useState<string | null>(null)

  const load = () => {
    api.fleet().then((result) => { setRows(result); setErr('') }).catch((e) => setErr(String(e)))
  }

  useEffect(() => {
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [])

  const destroy = async (tid: string) => {
    if (!window.confirm(`确认删除 Thread ${tid.slice(0, 8)}？相关运行记录和消息将一并删除，且不可恢复。`)) return
    setDestroying(tid)
    setErr('')
    try {
      await api.destroy(tid)
      load()
    } catch (e) {
      setErr(String(e))
    } finally {
      setDestroying(null)
    }
  }

  return (
    <div className="mx-auto max-w-6xl px-7 pb-20 pt-7">
      <header className="mb-6 flex items-end justify-between">
        <div>
          <div className="silk">MODULE <b>03</b> / THREADS</div>
          <h1 className="mt-1.5 text-[21px] font-semibold tracking-tight">Thread 列表</h1>
          <p className="mt-1 text-[12.5px] text-ink-2">查看 Agent 对话、运行状态与异常。</p>
        </div>
        <div className="silk">5S&nbsp;REFRESH</div>
      </header>

      {err && <ErrorBanner title="Thread 加载失败" error={err} className="mb-5" />}

      <Panel>
        <PanelHead>
          <span className="silk">THREADS</span>
          <span className="silk">{rows.length}&nbsp;THREADS</span>
        </PanelHead>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-hairline-soft text-left">
                <th className="silk whitespace-nowrap px-4 py-2.5 pl-5 font-medium">Thread</th>
                <th className="silk whitespace-nowrap px-4 py-2.5 font-medium">状态</th>
                <th className="silk whitespace-nowrap px-4 py-2.5 text-right font-medium">Token</th>
                <th className="silk whitespace-nowrap px-4 py-2.5 font-medium">最后活跃</th>
                <th className="silk whitespace-nowrap px-4 py-2.5 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.thread_id} className="border-b border-hairline-soft/60 transition-colors hover:bg-raised/40">
                  <td className="max-w-lg px-4 py-3 pl-5">
                    <Link to={`/threads/${r.thread_id}`} className="title-link block truncate font-medium text-ink">
                      {r.display_name || r.first_message?.slice(0, 60) || `Thread ${r.thread_id.slice(0, 8)}`}
                    </Link>
                    <div className="mt-0.5 font-mono text-xs text-ink-3" title={r.thread_id}>
                      {r.thread_id.slice(0, 8)} ·{' '}
                      {r.assistant_id && r.assistant_id !== 'lead_agent' ? (
                        <Link to={`/agents/${r.assistant_id}`} className="link-console">Agent：{r.assistant_id} →</Link>
                      ) : <span>未关联 Agent</span>}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    {r.last_run_status
                      ? (() => { const m = runStatusMeta(r.last_run_status); return <StatusPill color={m.led} blink={m.blink}>{m.label}</StatusPill> })()
                      : <StatusPill color="off">空闲</StatusPill>}
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-xs tabular-nums text-ink-2">{fmtTokens(r.total_tokens)}</td>
                  <td className="px-4 py-3 font-mono text-xs text-ink-3" title={fmtFullTime(r.last_run_at ?? r.updated_at)}>
                    {fmtTime(r.last_run_at ?? r.updated_at)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Button variant="destructive" size="sm" onClick={() => destroy(r.thread_id)} disabled={destroying === r.thread_id}>
                      {destroying === r.thread_id ? '删除中' : '删除'}
                    </Button>
                  </td>
                </tr>
              ))}
              {rows.length === 0 && !err && (
                <tr><td colSpan={5} className="px-6 py-12 text-center font-mono text-sm text-ink-3">暂无 Thread。</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  )
}
