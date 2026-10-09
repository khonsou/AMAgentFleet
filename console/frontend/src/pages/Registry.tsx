import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { api, type Overview, type RegistryAgent } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Panel, PanelBody, PanelHead } from '@/components/console/Panel'
import { StatusPill } from '@/components/console/Status'

function fmtTime(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

function fmtNum(n: number) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'k'
  return String(n)
}

const SELECT_CLS = 'ctl h-9 shrink-0 rounded-[2px] border border-hairline bg-inset px-2 font-mono text-xs text-ink'
const INPUT_CLS = 'rounded-[2px] border-hairline bg-inset text-ink placeholder:text-ink-3 focus-visible:ring-signal/40'
const FORM_INPUT_CLS = 'rounded-[2px] border-hairline bg-inset text-sm text-ink placeholder:text-ink-3 focus-visible:ring-signal/40'

export default function RegistryPage() {
  const [ov, setOv] = useState<Overview | null>(null)
  const [agents, setAgents] = useState<RegistryAgent[]>([])
  const [err, setErr] = useState('')
  const [instruction, setInstruction] = useState('')
  const [launchAgent, setLaunchAgent] = useState('')
  const [launching, setLaunching] = useState(false)
  const navigate = useNavigate()

  const load = () => {
    api.overview().then(setOv).catch((e) => setErr(String(e)))
    api.agents().then(setAgents).catch(() => setAgents([]))
  }

  useEffect(() => {
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const launch = async () => {
    if (launching || !launchAgent) return
    setLaunching(true)
    setErr('')
    try {
      await api.agentDispatch(launchAgent, instruction.trim() || '开始工作：请先介绍你的职责。')
      setInstruction('')
      navigate(`/agents/${launchAgent}`)
    } catch (e) {
      setErr(String(e))
      setLaunching(false)
    }
  }

  /** 注册新 agent：表单生成模板 YAML → 部署（YAML 落盘 + 导入网关 + 元数据入库）。 */
  const [showRegister, setShowRegister] = useState(false)
  const [reg, setReg] = useState({ name: '', displayName: '', owner: '', costCenter: '', description: '' })
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
          body: JSON.stringify({ templateId, name, displayName: reg.displayName.trim(), owner: reg.owner.trim(), costCenter: reg.costCenter.trim() }),
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
      setReg({ name: '', displayName: '', owner: '', costCenter: '', description: '' })
      navigate(`/agents/${name}`)
    } catch (e) {
      setErr(String(e))
    } finally {
      setRegistering(false)
    }
  }

  const readouts = [
    { label: 'AGENT 会话', value: ov?.threads },
    { label: '运行中', value: ov?.running, hot: true },
    { label: '异常任务', value: ov?.errored, bad: (ov?.errored ?? 0) > 0 },
    { label: '今日 TOKEN', value: ov ? fmtNum(ov.tokens_today) : undefined },
    { label: '累计 TOKEN', value: ov ? fmtNum(ov.tokens_total) : undefined },
    { label: 'CHECKPOINT', value: ov?.checkpoints },
  ]

  return (
    <div className="mx-auto max-w-7xl px-7 pb-20 pt-7">
      <header className="mb-6 flex items-end justify-between">
        <div>
          <div className="silk">MODULE <b>02</b> / REGISTRY</div>
          <h1 className="mt-1.5 text-[21px] font-semibold tracking-tight">Agent 定义</h1>
          <p className="mt-1 text-[12.5px] text-ink-2">
            注册 / 配置 / 派发 / 停止拉起。协作运行流见「Fleet 看板」，执行日志见「运行观测」。
          </p>
        </div>
        <div className="silk">5S&nbsp;REFRESH</div>
      </header>

      {/* 拉起 = 选 Registry agent 派发任务（不再创建裸 lead_agent 会话） */}
      <Panel className="mb-5">
        <PanelHead>
          <span className="silk">DISPATCH&nbsp;/&nbsp;派发</span>
          <span className="silk">ENTER&nbsp;↵</span>
        </PanelHead>
        <PanelBody className="flex items-center gap-2.5">
          <select
            value={launchAgent}
            onChange={(e) => setLaunchAgent(e.target.value)}
            className={SELECT_CLS + ' w-56'}
          >
            <option value="">选择 Registry Agent…</option>
            {agents.map((a) => (
              <option key={a.name} value={a.name}>{a.display_name || a.name}</option>
            ))}
          </select>
          <Input
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && launch()}
            placeholder="输入任务指令，派发该 agent 执行（新 thread）"
            className={INPUT_CLS}
            disabled={launching}
          />
          <Button variant="signal" onClick={launch} disabled={launching || !launchAgent} className="shrink-0">
            {launching ? '派发中…' : '派发任务'}
          </Button>
          <Button
            variant="outline"
            onClick={() => setShowRegister(!showRegister)}
            className="shrink-0"
          >
            + 注册新 Agent
          </Button>
        </PanelBody>
      </Panel>

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

      {/* 表盘读数带 */}
      <Panel className="mb-6">
        <div className="grid grid-cols-2 md:grid-cols-6">
          {readouts.map((c, i) => (
            <div key={c.label} className={i < readouts.length - 1 ? 'border-r border-hairline-soft px-4 pb-3 pt-3.5' : 'px-4 pb-3 pt-3.5'}>
              <span className="font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-ink-3">{c.label}</span>
              <div className={`mt-1 font-mono text-xl font-medium tabular-nums ${c.hot ? 'text-led-green' : c.bad ? 'text-led-red' : 'text-ink'}`}>
                {c.value ?? '—'}
              </div>
            </div>
          ))}
        </div>
      </Panel>

      <Panel className="mb-6">
        <PanelHead>
          <span className="silk">REGISTRY&nbsp;AGENTS&nbsp;/&nbsp;配置面 · 全员可见</span>
          <span className="silk">{agents.length}&nbsp;UNITS</span>
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
          运行观测（thread 执行日志，备用查询）→
        </Link>
      </div>
    </div>
  )
}
