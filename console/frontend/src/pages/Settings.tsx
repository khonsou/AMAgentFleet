import { useEffect, useState } from 'react'
import { api, type Pat } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Panel, PanelBody, PanelHead } from '@/components/console/Panel'
import { StatusPill } from '@/components/console/Status'

function fmtTime(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

const SELECT_CLS = 'ctl h-9 shrink-0 rounded-[2px] border border-hairline bg-inset px-2 font-mono text-xs text-ink'
const INPUT_CLS = 'rounded-[2px] border-hairline bg-inset text-sm text-ink placeholder:text-ink-3 focus-visible:ring-signal/40'

/** 设置：FAP PAT 签发管理（ADR-0011 H6）。
 *  PAT 是第三方 agent 访问控制面 API 的唯一凭证，scope 仅 read/write。 */
export default function SettingsPage() {
  const [pats, setPats] = useState<Pat[]>([])
  const [err, setErr] = useState('')
  const [name, setName] = useState('')
  const [scopeSel, setScopeSel] = useState<'read' | 'write'>('read')
  const [issuing, setIssuing] = useState(false)
  const [freshToken, setFreshToken] = useState<string | null>(null)

  const load = () => {
    api.pats().then(setPats).catch((e) => setErr(String(e)))
  }

  useEffect(() => {
    load()
  }, [])

  const issue = async () => {
    if (!name.trim()) return
    setIssuing(true)
    setErr('')
    setFreshToken(null)
    try {
      const resp = await api.patCreate(name.trim(), scopeSel)
      setFreshToken(resp.token)
      setName('')
      load()
    } catch (e) {
      setErr(String(e))
    } finally {
      setIssuing(false)
    }
  }

  const revoke = async (id: string, patName: string) => {
    if (!window.confirm(`确认吊销 PAT「${patName}」？吊销后使用该 token 的第三方调用立即 401。`)) return
    setErr('')
    try {
      await api.patRevoke(id)
      load()
    } catch (e) {
      setErr(String(e))
    }
  }

  const copyToken = () => {
    if (!freshToken) return
    navigator.clipboard.writeText(freshToken).catch(() => {})
  }

  return (
    <div className="mx-auto max-w-5xl px-7 pb-20 pt-7">
      <header className="mb-6 flex items-end justify-between">
        <div>
          <div className="silk">MODULE <b>04</b> / SETTINGS</div>
          <h1 className="mt-1.5 text-[21px] font-semibold tracking-tight">设置</h1>
          <p className="mt-1 text-[12.5px] text-ink-2">
            第三方 agent 接入（FAP）：签发 / 吊销 PAT。token 只显示一次，平台只存摘要。
          </p>
          <p className="mt-1.5 font-mono text-[12px] text-ink-2">
            API 接入地址（与本页同源）：<code className="text-led-green">{window.location.origin}</code>
            <span className="ml-2 text-ink-3">— 第三方 agent 先 GET <code>{window.location.origin}/api/meta</code> 自发现，详见接入指南 docs/fap-agent-guide.md</span>
          </p>
        </div>
      </header>

      {err && (
        <div className="mb-4 rounded-[2px] border border-signal/40 bg-signal/10 px-4 py-2.5 font-mono text-xs text-signal">
          {err}
        </div>
      )}

      <Panel>
        <PanelHead>
          <span className="silk">PAT / 签发新令牌</span>
        </PanelHead>
        <PanelBody>
          <div className="flex flex-wrap items-center gap-2.5">
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="用途标识，如 n8n-workflow"
              className={INPUT_CLS + ' w-64'}
            />
            <select
              value={scopeSel}
              onChange={(e) => setScopeSel(e.target.value as 'read' | 'write')}
              className={SELECT_CLS}
            >
              <option value="read">read（只读）</option>
              <option value="write">write（读写）</option>
            </select>
            <Button size="sm" onClick={issue} disabled={issuing || !name.trim()}>
              {issuing ? '签发中…' : '签发'}
            </Button>
          </div>
          {freshToken && (
            <div className="mt-4 rounded-[2px] border border-green-600/40 bg-green-500/10 px-4 py-3">
              <div className="silk mb-1.5 text-green-600">签发成功 · 只显示这一次，请立即保存</div>
              <div className="flex items-center gap-2">
                <code className="flex-1 break-all rounded-[2px] bg-inset px-3 py-2 font-mono text-xs text-ink">{freshToken}</code>
                <Button size="sm" variant="outline" onClick={copyToken}>复制</Button>
              </div>
            </div>
          )}
        </PanelBody>
      </Panel>

      <div className="mt-5">
        <Panel>
          <PanelHead>
            <span className="silk">PAT / 现存令牌（{pats.length}）</span>
            <button className="silk text-ink-3 transition-colors hover:text-ink" onClick={load}>↻ 刷新</button>
          </PanelHead>
          <PanelBody className="p-0">
            {pats.length === 0 ? (
              <div className="px-4 py-8 text-center text-[12.5px] text-ink-3">
                尚无 PAT。签发后第三方 agent 可凭 <code className="font-mono text-xs">Authorization: Bearer &lt;PAT&gt;</code> 调用控制面 API。
              </div>
            ) : (
              <table className="w-full text-left text-[12.5px]">
                <thead>
                  <tr className="border-b border-hairline-soft font-mono text-[10.5px] uppercase tracking-[0.08em] text-ink-3">
                    <th className="px-4 py-2.5 font-medium">名称</th>
                    <th className="px-4 py-2.5 font-medium">scope</th>
                    <th className="px-4 py-2.5 font-medium">签发</th>
                    <th className="px-4 py-2.5 font-medium">最近使用</th>
                    <th className="px-4 py-2.5 font-medium">状态</th>
                    <th className="px-4 py-2.5 text-right font-medium">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {pats.map((p) => (
                    <tr key={p.id} className="border-b border-hairline-soft/60 last:border-0">
                      <td className="px-4 py-2.5 font-mono text-xs">{p.name}</td>
                      <td className="px-4 py-2.5 font-mono text-xs">{p.scopes.join('+')}</td>
                      <td className="px-4 py-2.5 text-ink-2">{fmtTime(p.created_at)}</td>
                      <td className="px-4 py-2.5 text-ink-2">{fmtTime(p.last_used_at)}</td>
                      <td className="px-4 py-2.5">
                        <StatusPill color={p.revoked_at ? 'red' : 'green'}>
                          {p.revoked_at ? '已吊销' : '有效'}
                        </StatusPill>
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        {!p.revoked_at && (
                          <Button size="sm" variant="outline" onClick={() => revoke(p.id, p.name)}>吊销</Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </PanelBody>
        </Panel>
      </div>

      <p className="mt-4 text-[11.5px] leading-relaxed text-ink-3">
        自发现入口：<code className="font-mono">GET /api/meta</code>（免鉴权）；机器可读文档：<code className="font-mono">GET /api/agent-doc</code>。
        控制面未对公网开放前，PAT 仅在内网链路生效；公网暴露需等待 OIDC/PAT 全链路（P4，见 ADR-0011 第 5 条）。
      </p>
    </div>
  )
}
