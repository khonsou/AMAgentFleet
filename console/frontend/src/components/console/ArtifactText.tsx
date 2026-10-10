import LinkifyText from '@/components/LinkifyText'
import { api } from '@/lib/api'

/** 把文本中已知产物路径渲染成下载链接，其余部分照常 URL linkify。
 *  模型只需说路径（它不知道也不该知道 Console 地址——那是部署细节，
 *  随环境变化），URL 由前端拼，回复里的路径即点即下。 */
export default function ArtifactText({ text, tid, files, className }: {
  text: string; tid: string; files: string[]; className?: string
}) {
  if (files.length === 0) return <LinkifyText className={className} text={text} />
  const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const pattern = new RegExp(`(${files.map(esc).join('|')})`, 'g')
  const parts = text.split(pattern)
  return (
    <>
      {parts.map((part, i) =>
        files.includes(part) ? (
          <a key={i} href={api.artifactUrl(tid, part)} download={part.split('/').pop()}
            className="link-console font-mono text-[12.5px] underline decoration-led-green/60 underline-offset-2">
            {part}
          </a>
        ) : (
          <LinkifyText key={i} text={part} />
        )
      )}
    </>
  )
}
