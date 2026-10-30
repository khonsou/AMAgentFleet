import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import yaml from 'js-yaml'
import { api, type AgentDetail, type AgentTask } from '@/lib/api'
import { fmtTime, fmtTokens, fmtFullTime } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Panel, PanelBody, PanelHead, InsetRow } from '@/components/console/Panel'
import { StatusPill } from '@/components/console/Status'
import { agentStatusMeta, taskStatusMeta } from '@/components/console/statusMeta'
import CollapsibleContent from '@/components/console/CollapsibleContent'
import ErrorBanner from '@/components/console/ErrorBanner'

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
      setNotice('配置已保存并部署')
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

  /** 生命周期：停止/启动/删除。 */
  const lifecycle = async (action: 'stop' | 'start' | 'destroy') => {
    if (!name || lifecycling) return
    if (action === 'destroy' &&
        !window.confirm(`永久删除 Agent「${name}」及其配置？此操作不可撤销。`)) return
    setLifecycling(true)
    setErr('')
    setNotice('')
    try {
      if (action === 'stop') {
        const r = await api.agentStop(name)
        setNotice(`Agent 已停止，${r.paused_crons.length} 个定时任务已暂停`)
      } else if (action === 'start') {
        const r = await api.agentStart(name)
        setNotice(`Agent 已启动，${r.resumed_crons.length} 个定时任务已恢复`)
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
  const agentStatus = r?.status ? agentStatusMeta(r.status) : null

  return (
    <div className="mx-auto max-w-7xl px-7 pb-20 pt-7">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <Link to="/registry" className="link-console font-mono text-xs">← AGENT REGISTRY</Link>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight">
            {r?.display_name || name}
            <span className="ml-3 font-mono text-sm font-normal text-ink-3">{name}</span>
          </h1>
          <div className="mt-2 flex flex-wrap items-center gap-3 font-mono text-xs text-ink-3">
            <span>v{r?.config_version ?? '—'}</span>
            <span>负责人：{r?.owner || '—'}</span>
            {systemAgent && <span className="text-led-cyan">系统内置 · 配置只读</span>}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2.5 text-sm">
          {agentStatus && <StatusPill color={agentStatus.led} blink={agentStatus.blink}>{agentStatus.label}</StatusPill>}
          {!systemAgent && ((r?.status === 'registered' || r?.status === 'running') ? (
            <Button variant="outline" size="sm" disabled={lifecycling} onClick={() => lifecycle('stop')}
              className="border-led-amber/40 text-led-amber hover:bg-led-amber/10 hover:text-led-amber">
              {lifecycling ? '处理中…' : '停止'}
            </Button>
          ) : (
            <Button variant="outline" size="sm" disabled={lifecycling} onClick={() => lifecycle('start')}
              className="border-led-green/40 text-led-green hover:bg-led-green/10 hover:text-led-green">
              {lifecycling ? '处理中…' : '启动'}
            </Button>
          ))}
          {!systemAgent && (
            <Button variant="outline" size="sm" disabled={lifecycling} onClick={() => lifecycle('destroy')}
              className="border-led-red/40 text-led-red hover:bg-led-red/10 hover:text-led-red">删除</Button>
          )}
        </div>
      </div>

      {err && <ErrorBanner error={err} className="mb-4" />}
      {notice && (
        <div className="mb-4 rounded-[2px] border border-led-green/30 bg-led-green/10 px-4 py-3 text-sm text-led-green">{notice}</div>
      )}

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <Panel>
          <PanelHead>
            <span className="panel-title">Agent 配置 · YAML</span>
            {!systemAgent && (
              <Button size="sm" variant="signal" onClick={saveYaml} disabled={saving || !yamlDirty}
                className="h-7 px-2.5 text-[11px] disabled:opacity-40">
                {saving ? '保存中…' : '保存配置'}
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
            <div className="mt-2 flex flex-wrap justify-between gap-2 font-mono text-[11px] text-ink-3">
              <span>单次上限 {fmtTokens(Number(r?.budget?.maxTokensPerRun) || null)} · 每日上限 {fmtTokens(Number(r?.budget?.dailyTokenCap) || null)}</span>
              <span title={fmtFullTime(r?.synced_at)}>最近同步：{fmtTime(r?.synced_at)}</span>
            </div>
          </PanelBody>
        </Panel>

        <Panel className="flex flex-col">
          <PanelHead>
            <span className="panel-title">关联 Task</span>
            <span className="silk">{tasks.length}&nbsp;TASKS</span>
          </PanelHead>
          <PanelBody className="flex flex-1 flex-col">
            <div className="mb-4 border-b border-hairline-soft pb-4">
              {stopped && <div className="mb-2 text-xs text-led-amber">Agent 已停止，启动后可派发 Task。</div>}
              <Textarea value={input} onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) && dispatch()}
                rows={3} placeholder="输入 Task 指令…"
                className={INPUT_CLS + ' min-h-[72px] resize-y'} disabled={dispatching || stopped} />
              <div className="mt-2.5 flex items-center justify-between gap-2">
                <span className="font-mono text-[11px] text-ink-3">Ctrl / ⌘ + Enter 提交</span>
                <Button variant="signal" onClick={dispatch} disabled={dispatching || stopped || !input.trim()} className="shrink-0">
                  {dispatching ? '创建中…' : '指派并创建'}
                </Button>
              </div>
            </div>
            <div className="flex-1 space-y-2.5 overflow-y-auto pr-1" style={{ maxHeight: '520px' }}>
              {tasks.slice().reverse().map((t) => {
                const st = taskStatusMeta(t.status)
                return (
                  <InsetRow key={t.id} className="cursor-pointer transition-colors hover:border-ink-3" onClick={() => navigate(`/tasks/${t.id}`)}>
                    <div className="flex items-center justify-between gap-2.5 font-mono text-[11px] text-ink-3">
                      <Link to={`/tasks/${t.id}`} onClick={(e) => e.stopPropagation()}
                        className="title-link min-w-0 flex-1 truncate text-sm font-semibold tracking-tight text-ink" title={t.input || ''}>
                        {t.input?.trim() || '（无 Task 说明）'}
                      </Link>
                      <StatusPill color={st.led} blink={st.blink}>{st.label}</StatusPill>
                    </div>
                    <div className="mt-1 font-mono text-[10.5px] text-ink-3">Task #{t.id} · {t.source}</div>
                    {t.result_preview && (
                      <div className="mt-2" onClick={(e) => e.stopPropagation()}>
                        <div className="silk mb-1">OUTPUT</div>
                        <CollapsibleContent text={t.result_preview} collapseAfterChars={500} collapseAfterLines={3} previewLines={3}
                          className="block whitespace-pre-wrap text-xs leading-relaxed text-ink-2" />
                      </div>
                    )}
                    {t.error && (
                      <details className="mt-2 text-xs text-led-red" onClick={(e) => e.stopPropagation()}>
                        <summary className="cursor-pointer">异常 · 技术详情</summary>
                        <pre className="mt-1 whitespace-pre-wrap break-words">{t.error}</pre>
                      </details>
                    )}
                    <div className="mt-1.5 flex items-center justify-between font-mono text-[11px] text-ink-3">
                      <span className="tabular-nums">{fmtTokens(t.tokens)} Token</span>
                      <span title={fmtFullTime(t.created_at)}>{fmtTime(t.created_at)}</span>
                    </div>
                  </InsetRow>
                )
              })}
              {tasks.length === 0 && <div className="py-12 text-center font-mono text-sm text-ink-3">暂无关联 Task，可在上方发起。</div>}
              <div ref={bottomRef} />
            </div>
          </PanelBody>
        </Panel>
      </div>
    </div>
  )
}
