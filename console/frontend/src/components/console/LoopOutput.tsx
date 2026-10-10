import type { Loop } from '@/lib/api'
import ArtifactText from './ArtifactText'
import { Led } from './Status'

/** loop 当前产出展示（方案 A：展示派生）：
 *  主显 thread 最新 AI 回复（latest_output），thread 演化（打回续跑/人直接续对话）
 *  展示自动跟随；deliverable 降级为「交付时刻快照」低调标注（审计/追溯）。
 *  快照对比时剥掉 dispatcher 追加的 📎 产物段，避免产物链接造成假性「不一致」。 */
export default function LoopOutput({ l }: { l: Loop }) {
  const latest = l.latest_output?.trim()
  if (!latest && !l.deliverable) return null
  const snapText = l.deliverable?.split('\n\n📎 产物：')[0]?.trim()
  const showSnapshot = !!(latest && snapText && snapText !== latest)
  const files = l.artifacts ?? []
  const text = latest || l.deliverable || ''
  return (
    <div className="mt-2.5 rounded-[2px] border border-led-green/40 bg-led-green/[0.07] px-3.5 py-3">
      <div className="silk mb-2 flex items-center gap-2">
        <Led color="green" />
        OUTPUT&nbsp;/&nbsp;交付内容
      </div>
      <ArtifactText className="block whitespace-pre-wrap text-[13px] leading-relaxed text-ink"
        text={text} tid={l.thread_id ?? ''} files={files} />
      {showSnapshot && (
        <div className="mt-2 border-t border-led-green/20 pt-1.5 font-mono text-[10.5px] text-ink-3">
          交付快照 v{l.version}（入库时的交付记录）：
          <span className="line-clamp-1" title={l.deliverable ?? ''}>{l.deliverable}</span>
        </div>
      )}
    </div>
  )
}
