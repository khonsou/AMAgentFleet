export default function ErrorBanner({ title = '操作失败', error, className = '' }: {
  title?: string
  error: string
  className?: string
}) {
  return (
    <div role="alert" className={`rounded-[2px] border border-led-red/30 bg-led-red/10 px-4 py-3 text-sm text-led-red ${className}`}>
      <div className="font-medium">{title}</div>
      <details className="mt-1">
        <summary className="cursor-pointer select-none font-mono text-[11px] text-ink-2 hover:text-ink">技术详情</summary>
        <pre className="mt-2 whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-led-red">{error}</pre>
      </details>
    </div>
  )
}
