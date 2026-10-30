import type { Loop } from '@/lib/api'
import CollapsibleContent from './CollapsibleContent'
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
    <div className="mt-2.5 rounded-[2px] border border-hairline-soft bg-inset/60 px-3.5 py-3">
      <div className="panel-title mb-2 flex items-center gap-2">
        <Led color="green" />
        OUTPUT
      </div>
      <CollapsibleContent text={text} tid={l.thread_id ?? ''} files={files} />
      {showSnapshot && (
        <details className="mt-2 border-t border-hairline-soft pt-2 font-mono text-[10.5px] text-ink-3">
          <summary className="cursor-pointer select-none hover:text-ink">OUTPUT SNAPSHOT v{l.version}</summary>
          <CollapsibleContent className="mt-2 block whitespace-pre-wrap text-xs leading-relaxed text-ink-2"
            text={l.deliverable ?? ''} tid={l.thread_id ?? ''} files={files} />
        </details>
      )}
    </div>
  )
}
