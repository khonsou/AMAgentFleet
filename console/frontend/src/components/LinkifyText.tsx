/** 把文本中的 URL 渲染为可点击链接（交付物里的产物链接、人贴的飞书文档等）。 */

const URL_RE = /(https?:\/\/[^\s<>"')）】\]]+)/g

export default function LinkifyText({ text, className }: { text: string; className?: string }) {
  const parts = text.split(URL_RE)
  return (
    <span className={className}>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <a key={i} href={part} target="_blank" rel="noreferrer"
            className="text-sky-400 underline decoration-dotted hover:text-sky-300">
            {part}
          </a>
        ) : (
          <span key={i}>{part}</span>
        )
      )}
    </span>
  )
}
