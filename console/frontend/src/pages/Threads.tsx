import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { api, type FleetRow } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Panel, PanelHead } from '@/components/console/Panel'
import { StatusPill } from '@/components/console/Status'
import { runStatusMeta } from '@/components/console/statusMeta'

function fmtTime(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

function fmtNum(n: number) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'k'
  return String(n)
}

/** 运行观测（备用查询页）：thread 执行日志维度。
 *  日常 agent 管理请回 Registry；本页仅在排查执行问题时使用。 */
export default function ThreadsPage() {
  const [rows, setRows] = useState<FleetRow[]>([])
  const [err, setErr] = useState('')
  const [destroying, setDestroying] = useState<string | null>(null)

  const load = () => {
    api.fleet().then(setRows).catch((e) => setErr(String(e)))
  }

  useEffect(() => {
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [])

  const destroy = async (tid: string) => {
    if (!window.confirm(`确认销毁会话 ${tid.slice(0, 8)}？其 checkpoint、run 记录与消息将从数据库删除，不可恢复。`)) return
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
    <div className="mx-auto max-w-7xl px-7 pb-20 pt-7">
      <header className="mb-6 flex items-end justify-between">
        <div>
          <div className="silk">MODULE <b>03</b> / THREADS</div>
          <h1 className="mt-1.5 text-[21px] font-semibold tracking-tight">Thread 实例观测</h1>
          <p className="mt-1 text-[12.5px] text-ink-2">
            Thread 实例执行日志（备用查询）：工作会话从 Task → Loop 进入；Agent 配置请到 Registry 管理。
          </p>
        </div>
        <div className="silk">5S&nbsp;REFRESH</div>
      </header>

      {err && (
        <div className="mb-5 rounded-[2px] border border-led-red/30 bg-led-red/10 px-4 py-3 text-sm text-led-red">
          后端连接异常：{err}
        </div>
      )}

      <Panel>
        <PanelHead>
          <span className="silk">会话实例日志&nbsp;/&nbsp;THREAD&nbsp;LOG</span>
          <span className="silk">{rows.length}&nbsp;THREADS</span>
        </PanelHead>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-hairline-soft text-left">
                <th className="silk whitespace-nowrap px-4 py-2.5 pl-5 font-medium">会话 / 首条指令</th>
                <th className="silk whitespace-nowrap px-4 py-2.5 font-medium">状态</th>
                <th className="silk whitespace-nowrap px-4 py-2.5 text-right font-medium">Runs</th>
                <th className="silk whitespace-nowrap px-4 py-2.5 text-right font-medium">Checkpoints</th>
                <th className="silk whitespace-nowrap px-4 py-2.5 text-right font-medium">LLM 调用</th>
                <th className="silk whitespace-nowrap px-4 py-2.5 text-right font-medium">Token</th>
                <th className="silk whitespace-nowrap px-4 py-2.5 font-medium">最后活跃</th>
                <th className="silk whitespace-nowrap px-4 py-2.5 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.thread_id} className="border-b border-hairline-soft/60 transition-colors hover:bg-raised/40">
                  <td className="max-w-md px-4 py-3 pl-5">
                    <Link to={`/threads/${r.thread_id}`} className="block truncate font-medium text-ink">
                      {r.display_name || r.first_message?.slice(0, 60) || r.thread_id.slice(0, 8)}
                    </Link>
                    <div className="mt-0.5 font-mono text-xs text-ink-3">
                      {r.thread_id.slice(0, 8)} ·{' '}
                      {r.assistant_id && r.assistant_id !== 'lead_agent' ? (
                        <Link to={`/agents/${r.assistant_id}`} className="link-console">
                          Agent 定义：{r.assistant_id} →
                        </Link>
                      ) : (
                        <span title="spike 遗留的未注册会话，非 Registry agent">未注册会话</span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    {r.last_run_status
                      ? (() => { const m = runStatusMeta(r.last_run_status); return <StatusPill color={m.led} blink={m.blink}>{m.label}</StatusPill> })()
                      : <StatusPill color="off">空闲</StatusPill>}
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-xs tabular-nums text-ink-2">{r.run_count}</td>
                  <td className="px-4 py-3 text-right font-mono text-xs tabular-nums text-ink-2">{r.checkpoint_count}</td>
                  <td className="px-4 py-3 text-right font-mono text-xs tabular-nums text-ink-2">{r.llm_call_count}</td>
                  <td className="px-4 py-3 text-right font-mono text-xs tabular-nums text-ink-2">{fmtNum(r.total_tokens)}</td>
                  <td className="px-4 py-3 font-mono text-xs text-ink-3">{fmtTime(r.last_run_at ?? r.updated_at)}</td>
                  <td className="px-4 py-3 text-right">
                    <Button
                      variant="destructive"
                      size="sm"
                      onClick={() => destroy(r.thread_id)}
                      disabled={destroying === r.thread_id}
                    >
                      {destroying === r.thread_id ? '销毁中' : '销毁'}
                    </Button>
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-6 py-12 text-center font-mono text-sm text-ink-3">暂无会话记录。</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  )
}
