import { useEffect, useState } from 'react'
import { api, type OpsCredentialStatus, type Pat } from '@/lib/api'
import { AUTH_ENABLED, getOauthSession, oauthLogout } from '@/lib/auth'
import { fmtTime } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Panel, PanelBody, PanelHead } from '@/components/console/Panel'
import { StatusPill } from '@/components/console/Status'
import ModelSettings from '@/components/console/ModelSettings'
import ErrorBanner from '@/components/console/ErrorBanner'

const SELECT_CLS = 'ctl h-9 shrink-0 rounded-[2px] border border-hairline bg-inset px-2 font-mono text-xs text-ink'
const INPUT_CLS = 'rounded-[2px] border-hairline bg-inset text-sm text-ink placeholder:text-ink-3 focus-visible:ring-signal/40'

/** 设置：FAP PAT 签发管理（ADR-0011 H6）。
 *  PAT 是第三方 agent 访问控制面 API 的唯一凭证，scope 仅 read/write。 */
export default function SettingsPage() {
  const [pats, setPats] = useState<Pat[]>([])
  const [opsCredential, setOpsCredential] = useState<OpsCredentialStatus | null>(null)
  const [err, setErr] = useState('')
  const [name, setName] = useState('')
  const [scopeSel, setScopeSel] = useState<'read' | 'write'>('read')
  const [issuing, setIssuing] = useState(false)
  const [rotating, setRotating] = useState(false)
  const [freshToken, setFreshToken] = useState<string | null>(null)

  const load = () => {
    Promise.all([api.pats(), api.opsCredential()])
      .then(([patRows, credential]) => {
        setPats(patRows)
        setOpsCredential(credential)
      })
      .catch((e) => setErr(String(e)))
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

  const rotateOpsCredential = async () => {
    if (!window.confirm('确认轮换 Fleet 操作助手的系统 PAT？旧 token 将立即失效，新 token 不会显示。')) return
    setRotating(true)
    setErr('')
    try {
      const status = await api.opsCredentialRotate()
      setOpsCredential(status)
      load()
    } catch (e) {
      setErr(String(e))
    } finally {
      setRotating(false)
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
          <p className="mt-1 text-[12.5px] text-ink-2">管理模型 Profile、API Key 与第三方访问 PAT。</p>
        </div>
      </header>

      {err && <ErrorBanner error={err} className="mb-4" />}

      <ModelSettings />

      <div className="mb-5">
        <Panel>
          <PanelHead>
            <span className="silk">FLEET OPS / 系统内置凭证</span>
            <StatusPill color={opsCredential?.configured ? 'green' : 'off'}>
              {opsCredential?.configured ? '已配置' : '检查中'}
            </StatusPill>
          </PanelHead>
          <PanelBody>
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div>
                <div className="text-sm font-medium text-ink">Fleet 操作助手 · fleet-ops-01</div>
                <div className="mt-1.5 font-mono text-[11.5px] leading-relaxed text-ink-3">
                  固定权限 read+write · 不可吊销、删除或降权 · 原始 token 仅由控制面使用
                  {opsCredential && <><br />凭证版本 v{opsCredential.version} · 最近轮换 {fmtTime(opsCredential.rotated_at)}</>}
                </div>
              </div>
              <Button size="sm" variant="outline" onClick={rotateOpsCredential} disabled={rotating}>
                {rotating ? '轮换中…' : '人工轮换系统 PAT'}
              </Button>
            </div>
          </PanelBody>
        </Panel>
      </div>

      <Panel>
        <PanelHead>
          <span className="silk">PAT / 签发新令牌</span>
        </PanelHead>
        <PanelBody>
          <div className="flex flex-wrap items-end gap-2.5">
            <label className="block">
              <span className="mb-1.5 block font-mono text-[11px] text-ink-2">用途名称</span>
              <Input aria-label="PAT 用途名称" value={name} onChange={(e) => setName(e.target.value)}
                placeholder="如 n8n-workflow" className={INPUT_CLS + ' w-64'} />
            </label>
            <label className="block">
              <span className="mb-1.5 block font-mono text-[11px] text-ink-2">权限 Scope</span>
              <select aria-label="PAT 权限 Scope" value={scopeSel}
                onChange={(e) => setScopeSel(e.target.value as 'read' | 'write')} className={SELECT_CLS}>
                <option value="read">read（只读）</option>
                <option value="write">write（读写）</option>
              </select>
            </label>
            <Button size="sm" onClick={issue} disabled={issuing || !name.trim()}>{issuing ? '签发中…' : '签发 PAT'}</Button>
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
                      <td className="px-4 py-2.5 font-mono text-xs">
                        {p.name}{p.system && <span className="ml-2 text-led-cyan">系统内置</span>}
                      </td>
                      <td className="px-4 py-2.5 font-mono text-xs">{p.scopes.join('+')}</td>
                      <td className="px-4 py-2.5 text-ink-2">{fmtTime(p.created_at)}</td>
                      <td className="px-4 py-2.5 text-ink-2">{fmtTime(p.last_used_at)}</td>
                      <td className="px-4 py-2.5">
                        <StatusPill color={p.revoked_at ? 'red' : 'green'}>
                          {p.revoked_at ? '已吊销' : '有效'}
                        </StatusPill>
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        {!p.revoked_at && !p.system && (
                          <Button size="sm" variant="outline" onClick={() => revoke(p.id, p.name)}>吊销</Button>
                        )}
                        {p.system && <span className="font-mono text-[11px] text-ink-3">仅可轮换</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </PanelBody>
        </Panel>
      </div>

      <details className="mt-5 rounded-[2px] border border-hairline-soft bg-inset/40 px-4 py-3">
        <summary className="cursor-pointer select-none font-mono text-xs text-ink-2">第三方接入</summary>
        <div className="mt-3 space-y-2 text-[11.5px] leading-relaxed text-ink-3">
          <div>API Origin：<code className="break-all font-mono text-ink-2">{window.location.origin}</code></div>
          <div>自发现：<code className="font-mono">GET /api/meta</code>（免鉴权） · 机器文档：<code className="font-mono">GET /api/agent-doc</code></div>
          <div>接入指南：<code className="font-mono">docs/fap-agent-guide.md</code>。公网暴露前，PAT 仅在内网链路生效。</div>
        </div>
      </details>
      {AUTH_ENABLED && getOauthSession() && (
        <p className="mt-3 font-mono text-[12px] text-ink-2">
          员工身份（DAO OAuth）：
          <button onClick={oauthLogout} className="ml-2 text-signal underline decoration-dotted">退出登录</button>
        </p>
      )}
    </div>
  )
}
