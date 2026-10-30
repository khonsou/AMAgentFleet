import { useMemo, useState } from 'react'
import ArtifactText from './ArtifactText'

interface JsonContent {
  raw: string
  formatted: string
  summary: string
}

function parseJson(text: string): JsonContent | null {
  const trimmed = text.trim()
  const fenced = trimmed.match(/^```(?:json)?\s*\n?([\s\S]*?)\n?```$/i)
  const raw = (fenced?.[1] ?? trimmed).trim()
  if (!raw || !['{', '['].includes(raw[0])) return null
  try {
    const value = JSON.parse(raw) as unknown
    if (value === null || typeof value !== 'object') return null
    const summary = Array.isArray(value)
      ? `${value.length} 项`
      : `${Object.keys(value as Record<string, unknown>).length} 个字段`
    return { raw, formatted: JSON.stringify(value, null, 2), summary }
  } catch {
    return null
  }
}

interface CollapsibleContentProps {
  text: string
  tid?: string
  files?: string[]
  className?: string
  kind?: 'message' | 'tool'
  label?: string
  collapseAfterChars?: number
  collapseAfterLines?: number
  previewLines?: number
}

/** 长文本与 JSON 的统一展示：默认给摘要，完整内容始终可展开查看。 */
export default function CollapsibleContent({
  text,
  tid = '',
  files = [],
  className = 'block whitespace-pre-wrap break-words text-[13px] leading-relaxed text-ink',
  kind = 'message',
  label,
  collapseAfterChars = 1200,
  collapseAfterLines = 16,
  previewLines = 10,
}: CollapsibleContentProps) {
  const [expanded, setExpanded] = useState(false)
  const json = useMemo(() => parseJson(text), [text])
  const lines = useMemo(() => text.split('\n'), [text])

  if (kind === 'tool' || json) {
    const summary = kind === 'tool'
      ? `${label || 'TOOL'} · ${json?.summary ?? `${text.length} 字符`}`
      : `JSON · ${json?.summary ?? `${text.length} 字符`}`
    return (
      <details className="group rounded-[2px] border border-hairline-soft bg-inset/60">
        <summary className="cursor-pointer select-none px-3 py-2 font-mono text-[11px] text-ink-2 hover:text-ink">
          {summary}<span className="ml-2 text-ink-3">点击展开／收起</span>
        </summary>
        <div className="border-t border-hairline-soft p-3">
          {json ? (
            <>
              <pre className="overflow-x-auto whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-ink-2">{json.formatted}</pre>
              <button type="button" onClick={() => void navigator.clipboard.writeText(json.raw).catch(() => {})}
                className="link-console mt-2 font-mono text-[11px]">复制 JSON</button>
            </>
          ) : (
            <ArtifactText text={text} tid={tid} files={files} className={className} />
          )}
        </div>
      </details>
    )
  }

  const shouldCollapse = text.length > collapseAfterChars || lines.length > collapseAfterLines
  if (!shouldCollapse) return <ArtifactText text={text} tid={tid} files={files} className={className} />

  const preview = expanded
    ? text
    : lines.length > previewLines
      ? lines.slice(0, previewLines).join('\n')
      : text.slice(0, collapseAfterChars)
  return (
    <div>
      <ArtifactText text={preview} tid={tid} files={files} className={className} />
      <button type="button" onClick={() => setExpanded((value) => !value)}
        className="link-console mt-2 font-mono text-[11px]">
        {expanded ? '收起' : `展开全文 · ${lines.length} 行 / ${text.length} 字符`}
      </button>
    </div>
  )
}
