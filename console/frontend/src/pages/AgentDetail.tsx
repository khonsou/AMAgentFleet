import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import yaml from 'js-yaml'
import { api, type AgentDetail, type AgentTask } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Panel, PanelBody, PanelHead, InsetRow } from '@/components/console/Panel'
import { StatusPill, taskStatusMeta } from '@/components/console/Status'

function fmtTime(iso?: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

function fmtTokens(n?: number | null) {
  if (n == null) return '—'
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'k'
  return String(n)
}

const INPUT_CLS = 'rounded-[2px] border-hairline bg-inset text-ink placeholder:text-ink-3 focus-visible:ring-signal/40'

/** Agent 定义详情页：左侧 YAML / 生命周期，右侧关联任务实例。
 *  实例列表是横向筛选，不与 Agent 定义组成实例 breadcrumb。 */
export default function AgentDetailPage() {
  const { name } = useParams<{ name: string }>()
  const [detail, setDetail] = useState<AgentDetail | null>(null)
  const [yamlText, setYamlText] = useState('')
  const [yamlDirty, setYamlDirty] = useState(false)
  const [tasks, setTasks] = useState<AgentTask[]>([])
  const [input, setInput] = useState('')
  const [dispatching, setDispatching] = useState(false)
  const [saving, setSaving] = useState(false)
  const [lifecycling, setLifecycling] = useState(false)
  const [notice, setNotice] = useState('')
  const [err, setErr] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()

  const load = async () => {
    if (!name) return
    try {
      const [d, t] = await Promise.all([api.agent(name), api.agentTasks(name)])
      setDetail(d)
      setTasks(t)
      if (!yamlDirty) setYamlText(yaml.dump(d.yaml, { noRefs: true, lineWidth: 120 }))
      setErr('')
    } catch (e) {
      setErr(String(e))
    }
  }

  useEffect(() => {
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [tasks.length, dispatching])

  const saveYaml = async () => {
    if (!name) return
    setSaving(true)
    setErr('')
    setNotice('')
    try {
      const doc = yaml.load(yamlText)
      if (typeof doc !== 'object' || doc === null) throw new Error('YAML 解析结果不是对象')
      await api.agentUpdate(name, doc)
      setYamlDirty(false)
      setNotice('已保存并部署到网关（agents_api 同步 + config_version +1）')
      await load()
    } catch (e) {
      setErr(String(e))
    } finally {
      setSaving(false)
    }
  }

  const dispatch = async () => {
    if (!name || !input.trim() || dispatching) return
    setDispatching(true)
    setErr('')
    try {
      await api.agentDispatch(name, input.trim())
      setInput('')
      await load()
    } catch (e) {
      setErr(String(e))
    } finally {
      setDispatching(false)
    }
  }

  /** 生命周期：停止/拉起/销毁。停止后可改配置，拉起即恢复 cron + 派发。 */
  const lifecycle = async (action: 'stop' | 'start' | 'destroy') => {
    if (!name || lifecycling) return
    if (action === 'destroy' &&
        !window.confirm(`确认销毁 ${name}？其 YAML、网关配置与元数据将被清除，不可恢复。`)) return
    setLifecycling(true)
    setErr('')
    setNotice('')
    try {
      if (action === 'stop') {
        const r = await api.agentStop(name)
        setNotice(`已停止（暂停 cron ${r.paused_crons.length} 个；改配置不受影响，拉起后恢复）`)
      } else if (action === 'start') {
        const r = await api.agentStart(name)
        setNotice(`已拉起（恢复 cron ${r.resumed_crons.length} 个，可继续派发任务）`)
      } else {
        await api.agentDestroy(name)
        navigate('/registry')
        return
      }
      await load()
    } catch (e) {
      setErr(String(e))
    } finally {
      setLifecycling(false)
    }
  }

  const r = detail?.registry
  const systemAgent = name === 'fleet-ops-01'
  const stopped = r != null && r.status !== 'registered' && r.status !== 'running'
  const statusLed = r?.status === 'running' ? 'green' : r?.status === 'registered' ? 'cyan' : 'off'

  return (
    <div className="mx-auto max-w-7xl px-7 pb-20 pt-7">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <Link to="/registry" className="link-console font-mono text-xs">← AGENT REGISTRY</Link>
          <div className="silk mt-2">MODULE&nbsp;02&nbsp;/&nbsp;REGISTRY</div>
          <h1 className="mt-1.5 text-[21px] font-semibold tracking-tight">
            {r?.display_name || name}
            <span className="ml-3 font-mono text-sm font-normal text-ink-3">{name}</span>
          </h1>
        </div>
        <div className="flex flex-wrap items-center gap-2.5 text-sm">
          <StatusPill color="off">v{r?.config_version ?? '—'}</StatusPill>
          <StatusPill color={statusLed} blink={r?.status === 'running'}>{r?.status ?? '—'}</StatusPill>
          {systemAgent && <StatusPill color="cyan">系统内置 · 配置只读</StatusPill>}
          <span className="font-mono text-xs text-ink-3">owner: {r?.owner || '—'}</span>
          {!systemAgent && ((r?.status === 'registered' || r?.status === 'running') ? (
            <Button
              variant="outline" size="sm" disabled={lifecycling}
              onClick={() => lifecycle('stop')}
              className="border-led-amber/40 text-led-amber hover:bg-led-amber/10 hover:text-led-amber"
            >
              {lifecycling ? '处理中…' : '停止'}
            </Button>
          ) : (
            <Button
              variant="outline" size="sm" disabled={lifecycling}
              onClick={() => lifecycle('start')}
              className="border-led-green/40 text-led-green hover:bg-led-green/10 hover:text-led-green"
            >
              {lifecycling ? '处理中…' : '拉起'}
            </Button>
          ))}
          {!systemAgent && (
            <Button
              variant="destructive" size="sm" disabled={lifecycling}
              onClick={() => lifecycle('destroy')}
            >
              销毁
            </Button>
          )}
        </div>
      </div>

      {err && (
        <div className="mb-4 rounded-[2px] border border-led-red/30 bg-led-red/10 px-4 py-3 text-sm text-led-red">{err}</div>
      )}
      {notice && (
        <div className="mb-4 rounded-[2px] border border-led-green/30 bg-led-green/10 px-4 py-3 text-sm text-led-green">{notice}</div>
      )}

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        {/* 定义区：YAML、配置版本和生命周期 */}
        <Panel>
          <PanelHead>
            <span className="silk">AGENT DEFINITION&nbsp;/&nbsp;Agent 定义 · YAML{systemAgent ? ' · 系统内置只读' : ''}</span>
            {!systemAgent && (
              <Button
                size="sm"
                variant="signal"
                onClick={saveYaml}
                disabled={saving || !yamlDirty}
                className="h-7 px-2.5 text-[11px] disabled:opacity-40"
              >
                {saving ? '保存中…' : '保存并部署'}
              </Button>
            )}
          </PanelHead>
          <PanelBody>
            <Textarea
              value={yamlText}
              onChange={(e) => { setYamlText(e.target.value); setYamlDirty(true) }}
              readOnly={systemAgent}
              spellCheck={false}
              className="lcd min-h-[520px] resize-y rounded-[2px] border-hairline text-xs leading-relaxed text-ink focus-visible:ring-signal/40"
            />
            <div className="mt-2 flex justify-between font-mono text-[11px] text-ink-3">
              <span>budget: {JSON.stringify(r?.budget ?? {})}</span>
              <span>最近同步: {fmtTime(r?.synced_at)}</span>
            </div>
          </PanelBody>
        </Panel>

        {/* 关联实例区：按 Agent 定义筛选任务，不进入定义层级 */}
        <Panel className="flex flex-col">
          <PanelHead>
            <span className="silk">TASK INSTANCES&nbsp;/&nbsp;关联任务实例</span>
            <span className="silk">{tasks.length}&nbsp;TASKS</span>
          </PanelHead>
          <PanelBody className="flex flex-1 flex-col">
            <div className="flex-1 space-y-2.5 overflow-y-auto pr-1" style={{ maxHeight: '520px' }}>
              {tasks.slice().reverse().map((t) => {
                const st = taskStatusMeta(t.status)
                return (
                  <Link key={t.id} to={`/tasks/${t.id}`} className="block">
                    <InsetRow className="transition-colors hover:border-ink-3">
                      <div className="flex items-center justify-between gap-2.5 font-mono text-[11px] text-ink-3">
                        <span className="min-w-0 flex-1 truncate text-[12px] font-medium tracking-tight text-ink"
                          title={t.input || ''}>
                          {t.input?.trim() || '（无任务说明）'}
                        </span>
                        <StatusPill color={st.led} blink={st.blink}>{st.label}</StatusPill>
                      </div>
                      <div className="mt-1 font-mono text-[10.5px] text-ink-3">task #{t.id} · {t.source}</div>
                      {t.result_preview && (
                        <div className="mt-1.5 line-clamp-3 whitespace-pre-wrap text-xs leading-relaxed text-ink-2">
                          {t.result_preview}
                        </div>
                      )}
                      {t.error && (
                        <div className="mt-1 truncate text-xs text-led-red">{t.error}</div>
                      )}
                      <div className="mt-1.5 flex items-center justify-between font-mono text-[11px] text-ink-3">
                        <span className="tabular-nums">token: {fmtTokens(t.tokens)}</span>
                        <span>{fmtTime(t.created_at)}</span>
                      </div>
                      <div className="mt-1.5 flex items-center justify-between font-mono text-[10.5px] text-ink-3">
                        <span>进入任务查看执行轮次与会话</span>
                        <span>task #{t.id} 实例详情/操作 →</span>
                      </div>
                    </InsetRow>
                  </Link>
                )
              })}
              {tasks.length === 0 && (
                <div className="py-12 text-center font-mono text-sm text-ink-3">暂无关联任务实例。在下方输入指令发起。</div>
              )}
              <div ref={bottomRef} />
            </div>
            <div className="mt-4 border-t border-hairline-soft pt-4">
              <Textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) && dispatch()}
                rows={3}
                placeholder={(stopped ? 'Agent 已停止：先点上方"拉起"再发起任务实例。' : '输入指令发起任务实例。') + ' Ctrl/⌘+Enter 指派'}
                className={INPUT_CLS + ' min-h-[72px] resize-y'}
                disabled={dispatching || stopped}
              />
              <div className="mt-2.5 flex justify-end">
                <Button variant="signal" onClick={dispatch} disabled={dispatching || stopped} className="shrink-0">
                  {dispatching ? '派发中…' : '派发'}
                </Button>
              </div>
            </div>
          </PanelBody>
        </Panel>
      </div>
    </div>
  )
}
