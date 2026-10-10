import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { api, type AgentTask, type RegistryAgent, type TaskDetail, type Loop } from '@/lib/api'
import LinkifyText from '@/components/LinkifyText'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Panel, PanelBody, PanelHead, InsetRow } from '@/components/console/Panel'
import { Led, StatusPill, taskStatusMeta, loopStatusMeta, LOOP_AGG } from '@/components/console/Status'

/** Fleet 看板（Loop 视图）：以协作根任务为锚的运行流。
 *  指挥官在顶，worker 在下方按线程树流动；无父任务的是独立任务。
 *  设计参考：Hermes Kanban（根任务+扇出子任务同视图可见）/ agentboard（舰队级可见性）。
 *  视觉：机架仪器语言 —— 状态 = LED，数据 = 表盘读数，面板 = 模块。 */

type Tag = '待分配' | '执行中' | '待确认' | '已验收'
const TAGS: Tag[] = ['待分配', '执行中', '待确认', '已验收']
const TAG_LED: Record<Tag, 'amber' | 'green' | 'cyan' | 'off'> = {
  '待分配': 'amber', '执行中': 'green', '待确认': 'amber', '已验收': 'off',
}

/** 任务标签 = 你需要对它做什么（四态，池语义） */
export function tagOf(t: AgentTask): Tag {
  if (t.pool_status === 'done') return '已验收'
  if (t.pool_status === 'review') return '待确认'
  return t.agent_name === 'unassigned' ? '待分配' : '执行中'
}

const SELECT_CLS = 'ctl h-9 shrink-0 rounded-[2px] border border-hairline bg-inset px-2 font-mono text-xs text-ink'
const INPUT_CLS = 'rounded-[2px] border-hairline bg-inset text-ink placeholder:text-ink-3 focus-visible:ring-signal/40'

/** 待分配任务的行内认领（人/agent 同一入口，认领即离开待分配） */
function ClaimRow({ taskId, onChanged }: { taskId: number; onChanged: () => void }) {
  const [agents, setAgents] = useState<RegistryAgent[]>([])
  const [pick, setPick] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    api.agents().then((l) => setAgents(l.filter((a) => ['registered', 'running'].includes(a.status)))).catch(() => {})
  }, [])
  const claim = async () => {
    if (!pick || busy) return
    setBusy(true)
    try {
      const [type, who] = pick === '__me__' ? ['human', 'me'] as const : ['agent', pick] as const
      await api.taskClaim(taskId, type, who)
      onChanged()
    } finally { setBusy(false) }
  }
  return (
    <div className="mt-2.5 flex flex-wrap items-center gap-2.5 rounded-[2px] border border-dashed border-led-amber/35 px-3.5 py-2">
      <StatusPill color="amber" blink className="text-led-amber">待领取</StatusPill>
      <select value={pick} onChange={(e) => setPick(e.target.value)} className={SELECT_CLS + ' h-7 w-48'}>
        <option value="">选择领取者…</option>
        <option value="__me__">🧑 我（人工）</option>
        {agents.map((a) => <option key={a.name} value={a.name}>🤖 {a.display_name || a.name}</option>)}
      </select>
      <Button size="sm" variant="signal" disabled={busy || !pick} onClick={claim} className="h-7 px-2.5 text-[11px]">认领</Button>
      <Button size="sm" variant="destructive" disabled={busy}
        onClick={async () => { setBusy(true); try { await api.taskClose(taskId) } finally { setBusy(false); onChanged() } }}
        className="h-7 px-2.5 text-[11px]">关闭</Button>
    </div>
  )
}

function fmtTokens(n?: number | null) {
  if (n == null) return '—'
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'k'
  return String(n)
}

function fmtTime(iso?: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleTimeString('zh-CN', { hour12: false })
}

interface Node {
  task: AgentTask
  children: Node[]
}

function buildForest(tasks: AgentTask[]): Node[] {
  const byParent = new Map<number | null, AgentTask[]>()
  for (const t of tasks) {
    const key = t.parent_task_id ?? null
    if (!byParent.has(key)) byParent.set(key, [])
    byParent.get(key)!.push(t)
  }
  const mk = (t: AgentTask): Node => ({ task: t, children: (byParent.get(t.id) ?? []).map(mk) })
  // 根 = parent 不存在于本批任务的（含 parent_task_id 为 null）
  const ids = new Set(tasks.map((t) => t.id))
  const roots = tasks
    .filter((t) => t.parent_task_id == null || !ids.has(t.parent_task_id))
    .map(mk)
  return roots
}

function loopStatus(nodes: Node[]): string {
  const flat: AgentTask[] = []
  const walk = (n: Node) => { flat.push(n.task); n.children.forEach(walk) }
  nodes.forEach(walk)
  if (flat.some((t) => t.status === 'running' || t.status === 'dispatched' || t.status === 'queued')) return 'running'
  if (flat.some((t) => t.status === 'error')) return 'error'
  if (flat.every((t) => t.status === 'success')) return 'success'
  return 'mixed'
}

function TaskNode({ node, depth }: { node: Node; depth: number }) {
  const t = node.task
  const st = taskStatusMeta(t.status)
  return (
    <div>
      <Link to={`/tasks/${t.id}`} className="block">
        <InsetRow className="transition-colors hover:border-ink-3">
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
            <Led color={st.led} blink={st.blink} />
            {t.agent_name === 'unassigned' && t.pool_status === 'open' ? (
              <StatusPill color="amber" className="text-led-amber">待领取</StatusPill>
            ) : t.agent_name === 'human' ? (
              <span className="font-mono text-xs text-ink-2">🧑 人工</span>
            ) : t.agent_name === 'unassigned' ? (
              <span className="font-mono text-[10px] text-ink-3">未认领</span>
            ) : (
              <Link to={`/agents/${t.agent_name}`} onClick={(e) => e.stopPropagation()}
                className="link-console font-mono text-xs text-ink">{t.agent_name}</Link>
            )}
            <span className="font-mono text-[11px] text-ink-3">#{t.id} · {t.source}</span>
            <StatusPill color={st.led} blink={st.blink}>{st.label}</StatusPill>
            <span className="ml-auto font-mono text-[11px] tabular-nums text-ink-3">
              {fmtTokens(t.tokens)} tok · {fmtTime(t.created_at)}
            </span>
          </div>
          {t.result_preview && (
            <div className="mt-1.5 line-clamp-2 whitespace-pre-wrap text-xs leading-relaxed text-ink-2">{t.result_preview}</div>
          )}
          {t.error && <div className="mt-1 truncate text-xs text-led-red">{t.error}</div>}
          <div className="mt-1.5 flex items-center justify-between font-mono text-[10.5px] text-ink-3">
            {t.thread_id ? (
              <Link to={`/threads/${t.thread_id}`} onClick={(e) => e.stopPropagation()}
                className="link-console">thread {t.thread_id.slice(0, 8)} →</Link>
            ) : <span>{t.status === 'queued' ? '未开始' : ''}</span>}
            <span>task #{t.id} 详情 →</span>
          </div>
        </InsetRow>
      </Link>
      {node.children.length > 0 && (
        <div className="task-rail">
          {node.children.map((c) => <TaskNode key={c.task.id} node={c} depth={depth + 1} />)}
        </div>
      )}
    </div>
  )
}

/** 验收面板：task 在 review 态时展开，并排比较各 loop 交付，采纳/退回。 */
function ReviewPanel({ taskId, onDone }: { taskId: number; onDone: () => void }) {
  const [detail, setDetail] = useState<TaskDetail | null>(null)
  const [comment, setComment] = useState('')
  const [target, setTarget] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const load = () => api.taskDetail(taskId).then(setDetail).catch((e) => setMsg(String(e)))
  useEffect(() => { load() }, [taskId])

  const act = async (fn: () => Promise<unknown>, okMsg: string) => {
    setBusy(true); setMsg('')
    try { await fn(); setMsg(okMsg); await load(); onDone() } catch (e) { setMsg(String(e)) } finally { setBusy(false) }
  }

  if (!detail) return <div className="py-2 font-mono text-xs text-ink-3">加载交付列表…</div>
  const delivered = detail.loops.filter((l) => ['delivered', 'returned', 'accepted'].includes(l.status))
  if (delivered.length === 0) return null

  return (
    <div className="mt-2.5 rounded-[2px] border border-led-amber/30 bg-led-amber/5 px-3.5 py-3">
      <div className="mb-2.5 flex items-center gap-2">
        <Led color="amber" />
        <span className="silk" style={{ color: 'hsl(var(--led-amber))' }}>验收台 · {delivered.length} 份交付可比较</span>
      </div>
      <div className="space-y-2">
        {delivered.map((l: Loop) => {
          const lm = loopStatusMeta(l.status)
          return (
            <InsetRow key={l.id}>
              <div className="flex flex-wrap items-center gap-2.5 font-mono text-[11px] text-ink-3">
                <span className="text-ink">{l.claimant}</span>
                <span>({l.claimant_type === 'human' ? '人' : 'agent'})</span>
                <StatusPill color={lm.led}>{lm.label} v{l.version}</StatusPill>
                <span className="ml-auto tabular-nums">{fmtTokens(l.tokens)} tok</span>
              </div>
              <div className="mt-2.5 rounded-[2px] border border-led-green/40 bg-led-green/[0.07] px-3.5 py-3">
                <div className="silk mb-2 flex items-center gap-2">
                  <Led color="green" />
                  OUTPUT&nbsp;/&nbsp;交付内容
                </div>
                <LinkifyText className="block whitespace-pre-wrap text-[13px] leading-relaxed text-ink" text={l.deliverable || ''} />
              </div>
              {l.thread_id && (
                <Link to={`/threads/${l.thread_id}`} className="link-console mt-1.5 inline-block font-mono text-[11px]">查看对话 →</Link>
              )}
              {detail.task.pool_status === 'review' && (
                <div className="mt-2.5 flex items-center gap-2">
                  <Button size="sm" variant="signal" disabled={busy}
                    onClick={() => act(() => api.taskAccept(taskId, l.id), `已采纳 loop #${l.id}，task 完成`)}
                    className="h-7 px-2.5 text-[11px]">采纳这份</Button>
                  <Textarea value={target === l.id ? comment : ''}
                    onChange={(e) => { setTarget(l.id); setComment(e.target.value) }}
                    rows={2}
                    placeholder="退回意见（对方同 thread 续跑修正）"
                    className={INPUT_CLS + ' min-w-64 resize-y text-xs'}
                    disabled={busy} />
                  <Button size="sm" variant="outline" disabled={busy || target !== l.id || !comment.trim()}
                    onClick={() => act(() => api.loopReturn(l.id, comment), `已退回 loop #${l.id}`)}
                    className="h-7 border-led-amber/40 px-2.5 text-[11px] text-led-amber hover:bg-led-amber/10 hover:text-led-amber">退回</Button>
                </div>
              )}
            </InsetRow>
          )
        })}
      </div>
      {msg && <div className="mt-2 font-mono text-xs text-led-green">{msg}</div>}
    </div>
  )
}

/** 读数带刻度（装饰）：按相对量级点亮 */
function Ticks({ value, max }: { value: number; max: number }) {
  const on = value <= 0 ? 0 : Math.max(1, Math.round((value / (max || 1)) * 8))
  return (
    <div className="ticks">
      {Array.from({ length: 8 }, (_, i) => <i key={i} className={i < on ? 'on' : ''} />)}
    </div>
  )
}

export default function Home() {
  const [tasks, setTasks] = useState<AgentTask[]>([])
  const [agents, setAgents] = useState<RegistryAgent[]>([])
  const [err, setErr] = useState('')
  const [launchAgent, setLaunchAgent] = useState('')
  const [selectedTags, setSelectedTags] = useState<Set<Tag>>(new Set(['待分配', '待确认']))
  const toggleTag = (tag: Tag) => setSelectedTags((prev) => {
    const next = new Set(prev)
    if (next.has(tag)) next.delete(tag); else next.add(tag)
    return next
  })
  const [instruction, setInstruction] = useState('')
  const [launching, setLaunching] = useState(false)

  const load = () => {
    api.allTasks(200).then(setTasks).catch((e) => setErr(String(e)))
    api.agents().then(setAgents).catch(() => setAgents([]))
  }

  useEffect(() => {
    load()
    const t = setInterval(load, 4000)
    return () => clearInterval(t)
  }, [])

  const launch = async () => {
    if (launching || !instruction.trim()) return
    setLaunching(true)
    setErr('')
    try {
      if (launchAgent) {
        await api.agentDispatch(launchAgent, instruction.trim())  // 指派给该 agent
      } else {
        await api.createTask(instruction.trim())                   // 入池待认领
      }
      setInstruction('')
      load()
    } catch (e) {
      setErr(String(e))
    } finally {
      setLaunching(false)
    }
  }

  const allForest = buildForest(tasks)
  // 树内任一节点命中选中标签即显示（如根已验收但子任务待确认，选「待确认」仍可见）
  const treeHasTag = (n: Node): boolean =>
    selectedTags.has(tagOf(n.task)) || n.children.some(treeHasTag)
  const forest = allForest.filter(treeHasTag)
  const runningLoops = tasks.filter((t) => tagOf(t) === '执行中').length
  const totalTokens = tasks.reduce((s, t) => s + (t.tokens ?? 0), 0)

  const readouts = [
    { label: 'RUNNING LOOPS', value: runningLoops, display: String(runningLoops), hot: true },
    { label: 'TASKS TOTAL', value: tasks.length, display: String(tasks.length) },
    { label: 'AGENTS REG', value: agents.length, display: String(agents.length) },
    { label: 'TOKENS CUM', value: totalTokens, display: fmtTokens(totalTokens) },
  ]
  const readoutMax = Math.max(...readouts.map((r) => r.value), 1)

  return (
    <div className="mx-auto max-w-7xl px-7 pb-20 pt-7">
      {/* 页头 */}
      <header className="mb-6 flex items-end justify-between">
        <div>
          <div className="silk">MODULE <b>01</b> / FLEET BOARD</div>
          <h1 className="mt-1.5 text-[21px] font-semibold tracking-tight">任务看板</h1>
          <p className="mt-1 text-[12.5px] text-ink-2">
            协作运行流：指挥官发起的 loop 在顶部，worker 在下方流动。点击任意节点看完整对话。
          </p>
        </div>
        <div className="silk">4S&nbsp;REFRESH</div>
      </header>

      {/* 发起新 loop */}
      <Panel className="mb-5">
        <PanelHead>
          <span className="silk">NEW&nbsp;LOOP&nbsp;/&nbsp;发起</span>
          <span className="silk">CTRL+ENTER&nbsp;↵</span>
        </PanelHead>
        <PanelBody>
          <Textarea value={instruction} onChange={(e) => setInstruction(e.target.value)}
            onKeyDown={(e) => (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) && launch()}
            rows={3}
            placeholder="输入任务指令，发起一个 loop（该 agent 可再派生子任务给其它 agent）。支持多行，Ctrl/⌘+Enter 提交。"
            className={INPUT_CLS + ' min-h-[72px] resize-y'} disabled={launching} />
          <div className="mt-2.5 flex items-center gap-2.5">
            <select value={launchAgent} onChange={(e) => setLaunchAgent(e.target.value)}
              className={SELECT_CLS + ' w-56'}>
              <option value="">不入池指派 · 先放任务池</option>
              {agents.map((a) => <option key={a.name} value={a.name}>{a.display_name || a.name}</option>)}
            </select>
            <Button variant="signal" onClick={launch} disabled={launching || !instruction.trim()} className="shrink-0">
              {launching ? '发起中…' : launchAgent ? '指派并发起' : '放入任务池'}
            </Button>
          </div>
        </PanelBody>
      </Panel>

      {err && (
        <div className="mb-4 rounded-[2px] border border-led-red/30 bg-led-red/10 px-4 py-3 text-sm text-led-red">{err}</div>
      )}

      {/* 表盘读数带 */}
      <Panel className="mb-1.5">
        <div className="grid grid-cols-2 md:grid-cols-4">
          {readouts.map((c, i) => (
            <div key={c.label} className={i < readouts.length - 1 ? 'border-r border-hairline-soft px-5 pb-3 pt-3.5' : 'px-5 pb-3 pt-3.5'}>
              <span className="inline-flex items-center gap-1.5 font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-ink-3">
                {c.hot && <Led color="green" />}{c.label}
              </span>
              <div className={`mt-1 font-mono text-[26px] font-medium tabular-nums ${c.hot ? 'text-led-green' : 'text-ink'}`}>
                {c.display}
              </div>
              <Ticks value={c.value} max={readoutMax} />
            </div>
          ))}
        </div>
      </Panel>

      {/* 过滤开关 */}
      <div className="mb-3 mt-5 flex flex-wrap items-center gap-2">
        <span className="silk mr-1.5">TASK&nbsp;STREAM</span>
        {TAGS.map((tag) => {
          const on = selectedTags.has(tag)
          return (
            <button key={tag} onClick={() => toggleTag(tag)}
              className={`inline-flex items-center gap-1.5 rounded-[2px] border px-3 py-1 font-mono text-[11px] tracking-wide transition-colors ${
                on ? 'border-ink-3 bg-raised text-ink' : 'border-hairline text-ink-3 hover:text-ink-2'
              }`}>
              <Led color={on ? TAG_LED[tag] : 'off'} blink={on && tag === '执行中'} />
              {tag}
              <span className="text-ink-3">{tasks.filter((t) => tagOf(t) === tag).length}</span>
            </button>
          )
        })}
        <span className="ml-auto font-mono text-[11px] text-ink-3">默认显示需要你行动的（待分配 / 待确认）</span>
      </div>

      {/* Loop 流 */}
      <div className="space-y-4">
        {forest.map((node) => {
          const st = loopStatus([node])
          const agg = LOOP_AGG[st] ?? { label: st, led: 'off' as const }
          const flat: AgentTask[] = []
          const walk = (n: Node) => { flat.push(n.task); n.children.forEach(walk) }
          walk(node)
          const tokens = flat.reduce((s, t) => s + (t.tokens ?? 0), 0)
          return (
            <Panel key={node.task.id}>
              <PanelHead>
                <div className="flex flex-wrap items-center gap-3.5">
                  <span className="max-w-[520px] truncate text-[13px] font-medium tracking-tight text-ink"
                    title={node.task.input || ''}>
                    {node.task.input?.trim() || '（无任务说明）'}
                  </span>
                  <span className="font-mono text-[11px] text-ink-3">loop #{node.task.id}</span>
                  {node.task.pool_status === 'review' && <StatusPill color="amber" className="text-led-amber">待验收</StatusPill>}
                  {node.task.pool_status === 'done' && <StatusPill color="green">已验收</StatusPill>}
                  <StatusPill color={agg.led} blink={agg.blink}>{agg.label}</StatusPill>
                  <span className="font-mono text-[11px] text-ink-3">
                    {flat.length} 任务 · {fmtTokens(tokens)} tok
                    {node.task.created_at ? ` · 始于 ${fmtTime(node.task.created_at)}` : ''}
                  </span>
                </div>
                {node.task.agent_name === 'unassigned' && node.task.pool_status === 'open' ? (
                  <span className="font-mono text-[11px] text-led-amber">开放任务</span>
                ) : node.task.agent_name === 'human' ? (
                  <span className="font-mono text-[11px] text-ink-2">🧑 人工领取</span>
                ) : node.task.agent_name === 'unassigned' ? (
                  <span className="font-mono text-[11px] text-ink-3">未认领</span>
                ) : (
                  <Link to={`/agents/${node.task.agent_name}`} className="link-console font-mono text-[11px]">
                    指挥官 {node.task.agent_name} →
                  </Link>
                )}
              </PanelHead>
              <PanelBody>
                <TaskNode node={node} depth={0} />
                {tagOf(node.task) === '待分配' && (
                  <ClaimRow taskId={node.task.id} onChanged={load} />
                )}
                {node.task.pool_status === 'review' && (
                  <ReviewPanel taskId={node.task.id} onDone={load} />
                )}
              </PanelBody>
            </Panel>
          )
        })}
        {forest.length === 0 && (
          <Panel>
            <PanelBody className="py-16 text-center font-mono text-sm text-ink-3">
              当前筛选下暂无任务。换个标签看看，或在上方发起新任务。
            </PanelBody>
          </Panel>
        )}
      </div>
    </div>
  )
}
