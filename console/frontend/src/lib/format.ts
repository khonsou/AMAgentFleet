export function fmtFullTime(iso?: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

export function fmtTime(iso?: string | null) {
  if (!iso) return '—'
  const date = new Date(iso)
  const diff = Date.now() - date.getTime()
  const minutes = Math.floor(diff / 60_000)
  if (minutes >= 0 && minutes < 1) return '刚刚'
  if (minutes >= 1 && minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours >= 1 && hours < 24) return `${hours} 小时前`
  const now = new Date()
  const sameYear = now.getFullYear() === date.getFullYear()
  return date.toLocaleString('zh-CN', {
    hour12: false,
    ...(sameYear ? {} : { year: 'numeric' as const }),
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

export function fmtTokens(n?: number | null) {
  if (n == null) return '—'
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'k'
  return String(n)
}
