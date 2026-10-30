import { useEffect, useState } from 'react'
import { api, type ManagedModel, type ManagedModelInput } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Panel, PanelBody, PanelHead } from '@/components/console/Panel'
import { StatusPill } from '@/components/console/Status'
import ErrorBanner from '@/components/console/ErrorBanner'

function Field({ label, children, className = '' }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <label className={`block ${className}`}>
      <span className="mb-1.5 block font-mono text-[11px] text-ink-2">{label}</span>
      {children}
    </label>
  )
}

const INPUT_CLS = 'rounded-[2px] border-hairline bg-inset text-sm text-ink placeholder:text-ink-3 focus-visible:ring-signal/40'
const EMPTY: ManagedModelInput = {
  name: '', display_name: '', provider: 'openai-compatible', model: '', base_url: '',
  enabled: true, supports_vision: false, context_window: null, max_tokens: null,
}

/** Gateway 原生 Managed Models：Profile 与 API Key 一起加密保存，响应永不回显密钥。 */
export default function ModelSettings() {
  const [rows, setRows] = useState<ManagedModel[]>([])
  const [form, setForm] = useState<ManagedModelInput>(EMPTY)
  const [revision, setRevision] = useState<string | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [notice, setNotice] = useState('')

  const load = () => api.managedModels()
    .then((result) => { setRows(result.models); setErr('') })
    .catch((e) => setErr(String(e)))

  useEffect(() => { load() }, [])

  const set = <K extends keyof ManagedModelInput>(key: K, value: ManagedModelInput[K]) => {
    setForm((current) => ({ ...current, [key]: value }))
  }

  const edit = (row: ManagedModel) => {
    if (row.source !== 'managed') return
    setRevision(row.revision || null)
    setApiKey('')
    setNotice('')
    setErr('')
    setForm({
      name: row.name, display_name: row.display_name || row.name,
      provider: 'openai-compatible', model: row.model, base_url: row.base_url || '',
      enabled: row.enabled, supports_vision: Boolean(row.supports_vision),
      context_window: row.context_window ?? null, max_tokens: row.max_tokens ?? null,
    })
  }

  const payload = (): ManagedModelInput => ({
    ...form,
    name: form.name.trim(), display_name: form.display_name.trim(),
    model: form.model.trim(), base_url: form.base_url.trim(),
    ...(apiKey ? { api_key: apiKey } : {}),
  })

  const save = async () => {
    if (busy || !form.name.trim() || !form.model.trim() || !form.base_url.trim()) return
    setBusy(true); setErr(''); setNotice('')
    try {
      await api.managedModelSave(payload(), revision)
      setNotice(revision ? (apiKey ? '模型与 API Key 已更新。' : '模型修改已保存。') : '模型已添加。')
      setForm(EMPTY); setRevision(null); setApiKey('')
      load()
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }

  const test = async () => {
    if (busy || !form.name.trim() || !form.model.trim() || !form.base_url.trim()) return
    setBusy(true); setErr(''); setNotice('')
    try {
      const result = await api.managedModelTest(payload(), revision)
      if (result.ok) setNotice('连接测试通过。')
      else setErr(`连接测试失败：${result.message}`)
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }

  return (
    <div className="mb-5 space-y-5">
      <Panel>
        <PanelHead><span className="silk">MODELS / 模型与独立 API KEY</span></PanelHead>
        <PanelBody>
          <p className="mb-4 text-[12px] leading-relaxed text-ink-2">API Key 加密保存且不回显；编辑时留空即保留原 Key。YAML 模型为只读。</p>
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Profile 名称">
              <Input aria-label="Profile 名称" placeholder="如 deepseek-reasoner" value={form.name}
                onChange={(e) => set('name', e.target.value)} disabled={Boolean(revision)} className={INPUT_CLS} />
            </Field>
            <Field label="显示名称">
              <Input aria-label="显示名称" placeholder="页面中展示的名称" value={form.display_name}
                onChange={(e) => set('display_name', e.target.value)} className={INPUT_CLS} />
            </Field>
            <Field label="Provider 模型">
              <Input aria-label="Provider 模型" placeholder="如 deepseek-reasoner" value={form.model}
                onChange={(e) => set('model', e.target.value)} className={INPUT_CLS} />
            </Field>
            <Field label="OpenAI 兼容地址">
              <Input aria-label="OpenAI 兼容地址" placeholder="https://api.deepseek.com" value={form.base_url}
                onChange={(e) => set('base_url', e.target.value)} className={INPUT_CLS} />
            </Field>
            <Field label={revision ? 'API Key（留空保留原 Key）' : 'API Key'}>
              <Input aria-label="API Key" type="password" autoComplete="new-password" placeholder="只写入，不回显" value={apiKey}
                onChange={(e) => setApiKey(e.target.value)} className={INPUT_CLS} />
            </Field>
            <Field label="最大输出 Token（可选）">
              <Input aria-label="最大输出 Token" type="number" placeholder="不限制" value={form.max_tokens ?? ''}
                onChange={(e) => set('max_tokens', e.target.value ? Number(e.target.value) : null)} className={INPUT_CLS} />
            </Field>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-2.5">
            <Button size="sm" onClick={save} disabled={busy || !form.name.trim() || !form.model.trim() || !form.base_url.trim()}>
              {busy ? '处理中…' : revision ? (apiKey ? '保存并轮换 Key' : '保存修改') : '添加模型'}
            </Button>
            <Button size="sm" variant="outline" onClick={test} disabled={busy || !form.name.trim() || !form.model.trim() || !form.base_url.trim()}>测试连接</Button>
            {revision && <Button size="sm" variant="ghost" onClick={() => { setForm(EMPTY); setRevision(null); setApiKey('') }}>取消编辑</Button>}
            <label className="ml-2 flex items-center gap-2 font-mono text-xs text-ink-2">
              <input type="checkbox" checked={form.enabled} onChange={(e) => set('enabled', e.target.checked)} />启用 Profile
            </label>
          </div>
          {notice && <div className="mt-3 font-mono text-xs text-led-green">{notice}</div>}
          {err && <ErrorBanner error={err} className="mt-3" />}
        </PanelBody>
      </Panel>

      <Panel>
        <PanelHead>
          <span className="silk">MODEL CATALOG / 已登记模型（{rows.length}）</span>
          <button className="silk text-ink-3 hover:text-ink" onClick={load}>↻ 刷新</button>
        </PanelHead>
        <PanelBody className="p-0">
          {rows.length === 0 && !err ? (
            <div className="px-4 py-8 text-center text-[12.5px] text-ink-3">暂无模型，请在上方添加。</div>
          ) : (
            <table className="w-full text-left text-[12.5px]">
              <thead><tr className="border-b border-hairline-soft font-mono text-[10.5px] text-ink-3">
                <th className="px-4 py-2.5 font-medium">Profile</th><th className="px-4 py-2.5 font-medium">模型</th>
                <th className="px-4 py-2.5 font-medium">来源</th><th className="px-4 py-2.5 font-medium">Key</th>
                <th className="px-4 py-2.5 font-medium">状态</th><th className="px-4 py-2.5" />
              </tr></thead>
              <tbody>{rows.map((row) => (
                <tr key={`${row.source}-${row.name}`} className="border-b border-hairline-soft/60 last:border-0">
                  <td className="px-4 py-2.5 font-mono text-xs">{row.display_name || row.name}<br /><span className="text-ink-3">{row.name}</span></td>
                  <td className="px-4 py-2.5 font-mono text-xs">{row.model}</td>
                  <td className="px-4 py-2.5">{row.source === 'config' ? '部署配置' : '页面管理'}</td>
                  <td className="px-4 py-2.5">{row.source === 'config' ? '环境托管' : row.has_api_key ? '已加密保存' : '未配置'}</td>
                  <td className="px-4 py-2.5"><StatusPill color={row.enabled && !row.conflict ? 'green' : 'red'}>{row.conflict ? '名称冲突' : row.enabled ? '可用' : '停用'}</StatusPill></td>
                  <td className="px-4 py-2.5 text-right">{row.source === 'managed' && <Button size="sm" variant="outline" onClick={() => edit(row)}>编辑</Button>}</td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </PanelBody>
      </Panel>
    </div>
  )
}
