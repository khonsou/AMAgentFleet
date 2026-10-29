import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { api, type RegistryAgent } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Panel, PanelBody, PanelHead } from '@/components/console/Panel'
import { StatusPill } from '@/components/console/Status'

function fmtTime(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

const SELECT_CLS = 'ctl h-9 shrink-0 rounded-[2px] border border-hairline bg-inset px-2 font-mono text-xs text-ink'
const FORM_INPUT_CLS = 'rounded-[2px] border-hairline bg-inset text-sm text-ink placeholder:text-ink-3 focus-visible:ring-signal/40'

export default function RegistryPage() {
  const [agents, setAgents] = useState<RegistryAgent[]>([])
  const [err, setErr] = useState('')
  const navigate = useNavigate()

  const load = () => {
    api.agents().then(setAgents).catch(() => setAgents([]))
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
          <h1 className="mt-1.5 text-[21px] font-semibold tracking-tight">Agent 定义</h1>
          <p className="mt-1 text-[12.5px] text-ink-2">
            注册 / 配置 / 停止拉起 Agent 定义。发起任务实例见「任务实例看板」，工作会话见「Thread 实例观测」。
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className="silk hidden md:inline">5S&nbsp;REFRESH</span>
          <Button variant="signal" onClick={() => setShowRegister(!showRegister)} className="shrink-0">
            + 注册新 Agent
          </Button>
        </div>
      </header>

      {showRegister && (
        <Panel className="mb-5 border-signal/40">
          <PanelHead>
            <span className="silk">REGISTER&nbsp;/&nbsp;注册新 Agent</span>
            <span className="silk hidden md:inline">YAML&nbsp;一等公民</span>
          </PanelHead>
          <PanelBody className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <p className="text-xs text-ink-3 md:col-span-2">创建定义 → 进 Registry，可在详情页继续改 YAML。</p>
            <select value={templateId} onChange={(e) => setTemplateId(e.target.value)}
              className={SELECT_CLS + ' w-full md:col-span-2'}>
              <option value="">空白模板（默认配置）</option>
              {tplList.map((t) => <option key={t.id} value={t.id}>模板：{t.id} — {t.description.slice(0, 40)}</option>)}
            </select>
            <Input value={reg.name} onChange={(e) => setReg({ ...reg, name: e.target.value })}
              placeholder="标识名（英文/数字/连字符，如 sales-followup-01）" className={FORM_INPUT_CLS + ' font-mono'} />
            <Input value={reg.displayName} onChange={(e) => setReg({ ...reg, displayName: e.target.value })}
              placeholder="显示名（如 销售跟进）" className={FORM_INPUT_CLS} />
            <Input value={reg.owner} onChange={(e) => setReg({ ...reg, owner: e.target.value })}
              placeholder="负责人/团队（如 sales-team）" className={FORM_INPUT_CLS} />
            <Input value={reg.costCenter} onChange={(e) => setReg({ ...reg, costCenter: e.target.value })}
              placeholder="成本中心（如 SAL-001，可空）" className={FORM_INPUT_CLS} />
            {!templateId && (
              <Input value={reg.description} onChange={(e) => setReg({ ...reg, description: e.target.value })}
                placeholder="职责描述（写入 SOUL 护栏，可空）" className={FORM_INPUT_CLS + ' md:col-span-2'} />
            )}
            {templateId && (
              <Input value={reg.egressDomain} onChange={(e) => setReg({ ...reg, egressDomain: e.target.value })}
                placeholder="目标站点域名（egress 白名单，如 data.example.com，可空）" className={FORM_INPUT_CLS + ' md:col-span-2'} />
            )}
            <div className="flex gap-2 md:col-span-2">
              <Button variant="signal" onClick={register} disabled={registering || !reg.name.trim()}>
                {registering ? '注册中…' : '创建并部署'}
              </Button>
              <Button variant="outline" onClick={() => setShowRegister(false)}>取消</Button>
            </div>
          </PanelBody>
        </Panel>
      )}

      {err && (
        <div className="mb-5 rounded-[2px] border border-led-red/30 bg-led-red/10 px-4 py-3 text-sm text-led-red">
          后端连接异常：{err}（确认 console backend :8002 与 DeerFlow 网关 :8001 已启动）
        </div>
      )}

      <Panel className="mb-6">
        <PanelHead>
          <span className="silk">REGISTRY&nbsp;AGENTS&nbsp;/&nbsp;配置面 · 全员可见</span>
          <span className="silk">{agents.length}&nbsp;AGENTS</span>
        </PanelHead>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-hairline-soft text-left">
                {['Agent', 'Owner / 成本中心', '状态', '配置版本', 'cron 触发', '最近同步'].map((h, i) => (
                  <th key={h} className={`silk whitespace-nowrap px-4 py-2.5 font-medium ${i >= 3 && i <= 4 ? 'text-right' : ''} ${i === 0 ? 'pl-5' : ''}`}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {agents.map((a) => (
                <tr key={a.name} className="border-b border-hairline-soft/60 transition-colors hover:bg-raised/40">
                  <td className="px-4 py-3 pl-5">
                    <Link to={`/agents/${a.name}`} className="block">
                      <div className="font-medium text-ink">{a.display_name || a.name}</div>
                      <div className="mt-0.5 font-mono text-xs text-ink-3">{a.name}</div>
                    </Link>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-ink-2">
                    {a.owner || '—'} / {a.cost_center || '—'}
                  </td>
                  <td className="px-4 py-3">
                    <StatusPill color={a.status === 'running' ? 'green' : a.status === 'registered' ? 'cyan' : 'off'}>
                      {a.status}
                    </StatusPill>
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-xs tabular-nums text-ink-2">v{a.config_version}</td>
                  <td className="px-4 py-3 text-right font-mono text-xs tabular-nums text-ink-2">
                    {Array.isArray((a.triggers as { cron?: unknown })?.cron)
                      ? ((a.triggers as { cron: unknown[] }).cron.length + ' 条')
                      : '—'}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-ink-3">{fmtTime(a.synced_at)}</td>
                </tr>
              ))}
              {agents.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-6 py-12 text-center font-mono text-sm text-ink-3">
                    Registry 暂无 agent（control-plane :8003 未启动或尚未部署）
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>

      {/* 运行观测已移至独立页 /threads，避免与 Registry 混淆 */}
      <div className="text-right">
        <Link to="/threads" className="link-console font-mono text-xs">
          Thread 实例观测（工作会话日志，备用查询）→
        </Link>
      </div>
    </div>
  )
}
