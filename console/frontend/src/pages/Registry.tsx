import { useEffect, useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router'
import { api, type RegistryAgent } from '@/lib/api'
import { fmtTime, fmtFullTime } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Panel, PanelBody, PanelHead } from '@/components/console/Panel'
import { StatusPill } from '@/components/console/Status'
import { agentStatusMeta } from '@/components/console/statusMeta'
import ErrorBanner from '@/components/console/ErrorBanner'

const SELECT_CLS = 'ctl h-9 w-full rounded-[2px] border border-hairline bg-inset px-2 font-mono text-xs text-ink'
const FORM_INPUT_CLS = 'w-full rounded-[2px] border-hairline bg-inset text-sm text-ink placeholder:text-ink-3 focus-visible:ring-signal/40'

function Field({ label, children, wide = false }: { label: string; children: ReactNode; wide?: boolean }) {
  return <label className={`block ${wide ? 'md:col-span-2' : ''}`}>
    <span className="mb-1.5 block text-xs font-medium text-ink-2">{label}</span>
    {children}
  </label>
}

export default function RegistryPage() {
  const [agents, setAgents] = useState<RegistryAgent[]>([])
  const [err, setErr] = useState('')
  const navigate = useNavigate()

  const load = () => {
    api.agents().then((list) => { setAgents(list); setErr('') }).catch((e) => { setAgents([]); setErr(String(e)) })
  }

  useEffect(() => {
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [])

  /** 注册新 agent：表单生成模板 YAML → 部署（YAML 落盘 + 导入网关 + 元数据入库）。 */
  const [showRegister, setShowRegister] = useState(false)
  const [reg, setReg] = useState({ name: '', displayName: '', owner: '', costCenter: '', description: '', egressDomain: '' })
  const [registering, setRegistering] = useState(false)
  const [templateId, setTemplateId] = useState('')
  const [tplList, setTplList] = useState<{ id: string; displayName: string; description: string }[]>([])
  useEffect(() => { api.templates().then(setTplList).catch(() => setTplList([])) }, [])

  const register = async () => {
    if (registering || !reg.name.trim()) return
    setRegistering(true)
    setErr('')
    try {
      const name = reg.name.trim().toLowerCase().replace(/[^a-z0-9-]/g, '-')
      let doc: Record<string, unknown>
      if (templateId) {
        const t = await fetch('/api/console/templates/render', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ templateId, name, displayName: reg.displayName.trim(), owner: reg.owner.trim(), costCenter: reg.costCenter.trim(), egressDomain: reg.egressDomain.trim() }),
        }).then((r) => { if (!r.ok) throw new Error(`${r.status}: template render failed`); return r.json() })
        doc = t.yaml as Record<string, unknown>
      } else {
      doc = {
        apiVersion: 'agentfleet/v1',
        kind: 'Agent',
        metadata: {
          name,
          displayName: reg.displayName.trim(),
          owner: reg.owner.trim(),
          costCenter: reg.costCenter.trim(),
        },
        spec: {
          format: 'deerflow.custom-agent',
          version: 1,
          agent: {
            name,
            display_name: reg.displayName.trim(),
            description: reg.description.trim(),
            model: 'deepseek-chat',
            model_settings: { temperature: 0.2 },
            skills: [],
            soul: reg.description.trim() || `你是 ${reg.displayName || name}。`,
            memory_enabled: true,
          },
        },
        triggers: { web: true, im: false, agentCallable: true, cron: [] },
        identity: { mode: 'obo', allowedCallers: reg.owner.trim() ? [reg.owner.trim()] : [] },
        budget: { maxTokensPerRun: 200000, dailyTokenCap: 2000000 },
        persistence: { checkpointTtlDays: 90, idleHibernateMinutes: 30 },
      }
      }
      await api.agentCreate(doc)
      setShowRegister(false)
      setReg({ name: '', displayName: '', owner: '', costCenter: '', description: '', egressDomain: '' })
      navigate(`/agents/${name}`)
    } catch (e) {
      setErr(String(e))
    } finally {
      setRegistering(false)
    }
  }

  return (
    <div className="mx-auto max-w-7xl px-7 pb-20 pt-7">
      <header className="mb-6 flex items-end justify-between">
        <div>
          <div className="silk">MODULE <b>02</b> / REGISTRY</div>
          <h1 className="mt-1.5 text-2xl font-semibold tracking-tight">Agent 定义</h1>
        </div>
        <div className="flex items-center gap-3">
          <span className="silk hidden md:inline">5S&nbsp;REFRESH</span>
          <Button variant="signal" onClick={() => setShowRegister(!showRegister)} className="shrink-0">
            + 注册 Agent
          </Button>
        </div>
      </header>

      {showRegister && (
        <Panel className="mb-5 border-signal/40">
          <PanelHead><span className="panel-title">注册 Agent</span></PanelHead>
          <PanelBody className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <p className="text-xs text-ink-2 md:col-span-2">创建后可继续编辑完整 YAML。</p>
            <Field label="模板" wide>
              <select value={templateId} onChange={(e) => setTemplateId(e.target.value)} className={SELECT_CLS}>
                <option value="">空白模板（默认配置）</option>
                {tplList.map((t) => <option key={t.id} value={t.id} title={t.description}>{t.displayName || t.id} — {t.description}</option>)}
              </select>
            </Field>
            <Field label="标识名">
              <Input value={reg.name} onChange={(e) => setReg({ ...reg, name: e.target.value })}
                placeholder="sales-followup-01" className={FORM_INPUT_CLS + ' font-mono'} />
            </Field>
            <Field label="显示名">
              <Input value={reg.displayName} onChange={(e) => setReg({ ...reg, displayName: e.target.value })}
                placeholder="销售跟进" className={FORM_INPUT_CLS} />
            </Field>
            <Field label="负责人／团队">
              <Input value={reg.owner} onChange={(e) => setReg({ ...reg, owner: e.target.value })}
                placeholder="sales-team" className={FORM_INPUT_CLS} />
            </Field>
            <Field label="成本中心（可选）">
              <Input value={reg.costCenter} onChange={(e) => setReg({ ...reg, costCenter: e.target.value })}
                placeholder="SAL-001" className={FORM_INPUT_CLS} />
            </Field>
            {!templateId && (
              <Field label="职责描述（可选）" wide>
                <Input value={reg.description} onChange={(e) => setReg({ ...reg, description: e.target.value })}
                  placeholder="说明该 Agent 的职责边界" className={FORM_INPUT_CLS} />
              </Field>
            )}
            {templateId && (
              <Field label="目标站点域名（可选）" wide>
                <Input value={reg.egressDomain} onChange={(e) => setReg({ ...reg, egressDomain: e.target.value })}
                  placeholder="data.example.com" className={FORM_INPUT_CLS} />
              </Field>
            )}
            <div className="flex gap-2 md:col-span-2">
              <Button variant="signal" onClick={register} disabled={registering || !reg.name.trim()}>
                {registering ? '注册中…' : '创建 Agent'}
              </Button>
              <Button variant="outline" onClick={() => setShowRegister(false)}>取消</Button>
            </div>
          </PanelBody>
        </Panel>
      )}

      {err && <ErrorBanner title="Agent 列表加载失败" error={err} className="mb-5" />}

      <Panel className="mb-6">
        <PanelHead>
          <span className="panel-title">全部 Agent</span>
          <span className="silk">{agents.length}&nbsp;AGENTS</span>
        </PanelHead>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-hairline-soft text-left">
                {['Agent', '负责人／成本中心', '状态', '版本', '定时任务', '最近同步'].map((h, i) => (
                  <th key={h} className={`silk whitespace-nowrap px-4 py-2.5 font-medium ${i >= 3 && i <= 4 ? 'text-right' : ''} ${i === 0 ? 'pl-5' : ''}`}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {agents.map((a) => {
                const status = agentStatusMeta(a.status)
                return (
                  <tr key={a.name} className="border-b border-hairline-soft/60 transition-colors hover:bg-raised/40">
                    <td className="px-4 py-3 pl-5">
                      <Link to={`/agents/${a.name}`} className="block">
                        <div className="font-medium text-ink">{a.display_name || a.name}</div>
                        <div className="mt-0.5 font-mono text-xs text-ink-3">{a.name}</div>
                      </Link>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-ink-2">
                      <div>{a.owner || '—'}</div><div className="mt-0.5 text-ink-3">{a.cost_center || '—'}</div>
                    </td>
                    <td className="px-4 py-3"><StatusPill color={status.led} blink={status.blink}>{status.label}</StatusPill></td>
                    <td className="px-4 py-3 text-right font-mono text-xs tabular-nums text-ink-2">v{a.config_version}</td>
                    <td className="px-4 py-3 text-right font-mono text-xs tabular-nums text-ink-2">
                      {Array.isArray((a.triggers as { cron?: unknown })?.cron)
                        ? ((a.triggers as { cron: unknown[] }).cron.length + ' 条')
                        : '—'}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-ink-3" title={fmtFullTime(a.synced_at)}>{fmtTime(a.synced_at)}</td>
                  </tr>
                )
              })}
              {agents.length === 0 && !err && (
                <tr><td colSpan={6} className="px-6 py-12 text-center font-mono text-sm text-ink-3">暂无 Agent，点击“注册 Agent”创建。</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  )
}
