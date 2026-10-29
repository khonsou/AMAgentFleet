import type { ChatMessage } from '@/lib/api'

function extractText(content: unknown): string {
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    return content.map((part) => typeof part === 'string' ? part : String(part?.text ?? '')).join('')
  }
  return ''
}

export function normalizeOpsMessages(raw: unknown): ChatMessage[] {
  const list = Array.isArray(raw) ? raw : ((raw as { messages?: unknown[] })?.messages ?? [])
  return (list as Record<string, unknown>[]).flatMap((message) => {
    const inner = (message.content && typeof message.content === 'object' ? message.content : message) as Record<string, unknown>
    const type = String(inner.type ?? message.event_type ?? '')
    const name = String(inner.name ?? message.name ?? '')
    const role = ['human', 'user', 'llm.human.input'].includes(type) ? 'human'
      : ['ai', 'assistant', 'llm.ai.response'].includes(type) || (type === 'tool' && name === 'ask_clarification') ? 'ai' : null
    const content = extractText(inner.content)
    return role && content.trim() ? [{ role, content }] : []
  })
}
