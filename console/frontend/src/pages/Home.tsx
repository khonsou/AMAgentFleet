import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { api, type AgentTask, type RegistryAgent, type TaskDetail, type Loop } from '@/lib/api'
import LoopOutput from '@/components/console/LoopOutput'
import CollapsibleContent from '@/components/console/CollapsibleContent'
import ErrorBanner from '@/components/console/ErrorBanner'
import { fmtTime, fmtTokens, fmtFullTime } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Panel, PanelBody, PanelHead, InsetRow } from '@/components/console/Panel'
import { Led, StatusPill } from '@/components/console/Status'
import { taskStatusMeta, loopStatusMeta, LOOP_AGG } from '@/components/console/statusMeta'

/** Fleet 任务看板：以协作根任务为锚的任务池。
 *  指挥官在顶，worker 在下方按线程树流动；无父任务的是独立任务。
 *  设计参考：Hermes Kanban（根任务+扇出子任务同视图可见）/ agentboard（舰队级可见性）。
 *  视觉：机架仪器语言 —— 状态 = LED，数据 = 表盘读数，面板 = 模块。 */

type Tag = '待领取' | '执行中' | '待验收' | '已结束'
const TAGS: Tag[] = ['待领取', '执行中', '待验收', '已结束']
const TAG_LED: Record<Tag, 'amber' | 'green' | 'cyan' | 'off'> = {
  '待领取': 'amber', '执行中': 'green', '待验收': 'amber', '已结束': 'off',
}

/** Task 标签 = 你需要对它做什么（四态，池语义） */
function tagOf(t: AgentTask): Tag {
  if (t.pool_status === 'done') return '已结束'
  if (t.pool_status === 'review') return '待验收'
  return t.agent_name === 'unassigned' ? '待领取' : '执行中'
}

const SELECT_CLS = 'ctl h-9 shrink-0 rounded-[2px] border border-hairline bg-inset px-2 font-mono text-xs text-ink'
const INPUT_CLS = 'rounded-[2px] border-hairline bg-inset text-ink placeholder:text-ink-3 focus-visible:ring-signal/40'

/** 待领取 Task 的行内领取（人/Agent 同一入口）。 */
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
      <StatusPill color="amber" className="text-led-amber">待领取</StatusPill>
      <select value={pick} onChange={(e) => setPick(e.target.value)} className={SELECT_CLS + ' h-7 w-48'}>
        <option value="">选择领取者…</option>
        <option value="__me__">🧑 我（人工）</option>
        {agents.map((a) => <option key={a.name} value={a.name}>🤖 {a.display_name || a.name}</option>)}
      </select>
      <Button size="sm" variant="signal" disabled={busy || !pick} onClick={claim} className="h-7 px-2.5 text-[11px]">领取</Button>
      <Button size="sm" variant="destructive" disabled={busy}
        onClick={async () => { setBusy(true); try { await api.taskClose(taskId) } finally { setBusy(false); onChanged() } }}
        className="h-7 px-2.5 text-[11px]">关闭 Task</Button>
    </div>
  )
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

function TaskNode({ node, depth, root = false }: { node: Node; depth: number; root?: boolean }) {
  const t = node.task
  const st = taskStatusMeta(t.status)
  const navigate = useNavigate()
  const hasAgentDefinition = !['unassigned', 'human'].includes(t.agent_name)
  const instanceRole = node.children.length > 0 ? '指挥官实例' : depth > 0 ? 'Subagent 实例' : 'Agent 实例'
  return (
    <div>
      <InsetRow
        className="cursor-pointer transition-colors hover:border-ink-3"
        onClick={() => navigate(`/tasks/${t.id}`)}
      >
        {!root && (
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
            <Led color={st.led} blink={st.blink} />
            <Link to={`/tasks/${t.id}`} onClick={(e) => e.stopPropagation()}
              className="title-link max-w-[620px] truncate text-sm font-semibold"
              title={t.input || `Task #${t.id}`}>
              Task #{t.id} · {t.input?.trim() || 'Task 详情'}
            </Link>
            <span className="font-mono text-[11px] text-ink-3">{t.source}</span>
            {t.agent_name === 'unassigned' && t.pool_status === 'open' && (
              <StatusPill color="amber" className="text-led-amber">待领取</StatusPill>
            )}
            <StatusPill color={st.led} blink={st.blink}>{st.label}</StatusPill>
            <span className="ml-auto font-mono text-[11px] tabular-nums text-ink-3" title={fmtFullTime(t.created_at)}>
              {fmtTokens(t.tokens)} Token · {fmtTime(t.created_at)}
            </span>
          </div>
        )}
        {t.result_preview && (
          <div className="mt-3 rounded-[2px] border border-hairline-soft bg-inset/60 px-3 py-2.5" onClick={(e) => e.stopPropagation()}>
            <div className="panel-title mb-1.5">{t.pool_status === 'done' && t.accepted_loop_id ? 'FINAL OUTPUT' : 'CURRENT OUTPUT'}</div>
            <CollapsibleContent text={t.result_preview} collapseAfterChars={500} collapseAfterLines={3} previewLines={3} />
          </div>
        )}
        {t.error && (
          <details className="mt-2 text-xs text-led-red" onClick={(e) => e.stopPropagation()}>
            <summary className="cursor-pointer">异常 · 技术详情</summary>
            <pre className="mt-1 whitespace-pre-wrap break-words">{t.error}</pre>
          </details>
        )}
        <div className="mt-2.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 border-t border-hairline-soft pt-2.5 font-mono text-[11px]">
          {t.latest_loop_id ? (
            <Link to={`/tasks/${t.id}#loop-${t.latest_loop_id}`} onClick={(e) => e.stopPropagation()}
              className="link-console text-ink-3">LOOP #{t.latest_loop_id}</Link>
          ) : <span className="text-ink-3">尚未产生 LOOP</span>}
          {t.latest_thread_id && t.latest_loop_id ? (
            <>
              <span className="text-ink-2">{t.agent_name === 'human' ? '人工实例' : instanceRole}</span>
              <Link to={`/threads/${t.latest_thread_id}`} onClick={(e) => e.stopPropagation()}
                className="link-console font-medium text-ink">Thread {t.latest_thread_id.slice(0, 8)} →</Link>
            </>
          ) : t.latest_loop_id ? <span className="text-ink-3">等待 Thread 启动</span> : null}
          {hasAgentDefinition && (
            <Link to={`/agents/${t.agent_name}`} onClick={(e) => e.stopPropagation()}
              className="link-console ml-auto text-[10.5px] font-normal text-ink-3">Agent：{t.agent_name} →</Link>
          )}
        </div>
      </InsetRow>
      {node.children.length > 0 && (
        <div className="task-rail">
          {node.children.map((c) => <TaskNode key={c.task.id} node={c} depth={depth + 1} />)}
        </div>
      )}
    </div>
  )
}

/** 验收面板：Task 在 review 态时展开，并排比较各 LOOP 交付。 */
function ReviewPanel({ taskId, onDone }: { taskId: number; onDone: () => void }) {
  const [detail, setDetail] = useState<TaskDetail | null>(null)
  const [comment, setComment] = useState('')
  const [target, setTarget] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(
    () => api.taskDetail(taskId).then(setDetail).catch((e) => setError(String(e))),
    [taskId],
  )
  useEffect(() => { load() }, [load])

  const act = async (fn: () => Promise<unknown>, okMsg: string) => {
    setBusy(true); setMsg(''); setError('')
    try { await fn(); setMsg(okMsg); await load(); onDone() } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }

  if (!detail) return <div className="py-2 font-mono text-xs text-ink-3">加载交付列表…</div>
  const delivered = detail.loops.filter((l) => ['delivered', 'returned', 'accepted'].includes(l.status))
  if (delivered.length === 0) return null

  return (
    <div className="mt-2.5 rounded-[2px] border border-led-amber/30 bg-led-amber/5 px-3.5 py-3">
      <div className="mb-2.5 flex items-center gap-2">
        <Led color="amber" />
        <span className="panel-title text-led-amber">验收 · {delivered.length} 份交付</span>
      </div>
      <div className="space-y-2">
        {delivered.map((l: Loop) => {
          const lm = loopStatusMeta(l.status)
          return (
            <InsetRow key={l.id}>
              <div className="flex flex-wrap items-center gap-2.5 font-mono text-[11px] text-ink-3">
                <span>LOOP #{l.id}</span>
                <span className="text-ink-2">{l.claimant_type === 'human' ? '人工实例' : 'Agent 实例'}</span>
                {l.thread_id && (
                  <Link to={`/threads/${l.thread_id}`} className="link-console font-medium text-ink">Thread {l.thread_id.slice(0, 8)} →</Link>
                )}
                <StatusPill color={lm.led}>{lm.label} v{l.version}</StatusPill>
                <span className="ml-auto tabular-nums">执行者：{l.claimant} · {fmtTokens(l.tokens)} Token</span>
              </div>
              {l.latest_output?.trim() || l.deliverable ? <LoopOutput l={l} /> : null}
              {detail.task.pool_status === 'review' && (
                <div className="mt-2.5 flex items-center gap-2">
                  <Button size="sm" variant="signal" disabled={busy}
                    onClick={() => act(() => api.taskAccept(taskId, l.id), `已采纳 LOOP #${l.id}，Task 完成`)}
                    className="h-7 px-2.5 text-[11px]">采纳交付</Button>
                  <Textarea value={target === l.id ? comment : ''}
                    onChange={(e) => { setTarget(l.id); setComment(e.target.value) }}
                    rows={2}
                    placeholder="填写退回原因；对方将在原 Thread 中修改"
                    className={INPUT_CLS + ' min-w-64 resize-y text-xs'}
                    disabled={busy} />
                  <Button size="sm" variant="outline" disabled={busy || target !== l.id || !comment.trim()}
                    onClick={() => act(() => api.loopReturn(l.id, comment), `已退回 LOOP #${l.id}`)}
                    className="h-7 border-led-amber/40 px-2.5 text-[11px] text-led-amber hover:bg-led-amber/10 hover:text-led-amber">退回</Button>
                </div>
              )}
            </InsetRow>
          )
        })}
      </div>
      {msg && <div className="mt-2 font-mono text-xs text-led-green">{msg}</div>}
      {error && <div className="mt-2 font-mono text-xs text-led-red">{error}</div>}
    </div>
  )
}

export default function Home() {
  const [tasks, setTasks] = useState<AgentTask[]>([])
  const [agents, setAgents] = useState<RegistryAgent[]>([])
  const [err, setErr] = useState('')
  const [launchAgent, setLaunchAgent] = useState('')
  const [selectedTags, setSelectedTags] = useState<Set<Tag>>(new Set(['待领取', '待验收']))
  const toggleTag = (tag: Tag) => setSelectedTags((prev) => {
    const next = new Set(prev)
    if (next.has(tag)) next.delete(tag); else next.add(tag)
    return next
  })
  const [instruction, setInstruction] = useState('')
  const [launching, setLaunching] = useState(false)
  const [modelProfiles, setModelProfiles] = useState<Awaited<ReturnType<typeof api.models>>>([])
  const [modelProfile, setModelProfile] = useState('')
  const [subagentModelPolicy, setSubagentModelPolicy] = useState<'auto' | 'inherit'>('auto')

  const load = () => {
    api.allTasks(200).then((list) => { setTasks(list); setErr('') }).catch((e) => setErr(String(e)))
    api.agents().then(setAgents).catch(() => setAgents([]))
  }

  useEffect(() => {
    load()
    api.models().then(setModelProfiles).catch(() => setModelProfiles([]))
    const t = setInterval(load, 15000)
    return () => clearInterval(t)
  }, [])

  const launch = async () => {
    if (launching || !instruction.trim()) return
    setLaunching(true)
    setErr('')
    try {
      if (launchAgent) {
        await api.agentDispatch(launchAgent, instruction.trim(), modelProfile || undefined, subagentModelPolicy)
      } else {
        await api.createTask(instruction.trim(), undefined, modelProfile || undefined, subagentModelPolicy)
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
  const treeHasTag = (n: Node): boolean =>
    selectedTags.has(tagOf(n.task)) || n.children.some(treeHasTag)
  const forest = allForest.filter(treeHasTag)
  const readouts: { label: string; value: number; color: 'amber' | 'green' | 'red' }[] = [
    ...(['待领取', '待验收', '执行中'] as Tag[]).map((tag) => ({
      label: tag as string,
      value: tasks.filter((t) => tagOf(t) === tag).length,
      color: tag === '执行中' ? 'green' as const : 'amber' as const,
    })),
    { label: '异常', value: tasks.filter((t) => t.status === 'error').length, color: 'red' as const },
  ]

  return (
    <div className="mx-auto max-w-7xl px-7 pb-20 pt-7">
      <header className="mb-6 flex items-end justify-between">
        <div>
          <div className="silk">MODULE <b>01</b> / FLEET BOARD</div>
          <h1 className="mt-1.5 text-2xl font-semibold tracking-tight">Task 看板</h1>
        </div>
        <div className="silk">15S&nbsp;REFRESH</div>
      </header>

      <Panel className="mb-5">
        <PanelHead><span className="panel-title">发起 Task</span></PanelHead>
        <PanelBody>
          <Textarea value={instruction} onChange={(e) => setInstruction(e.target.value)}
            onKeyDown={(e) => (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) && launch()}
            rows={3} placeholder="输入 Task 指令…"
            className={INPUT_CLS + ' min-h-[72px] resize-y'} disabled={launching} />
          <div className="mt-2.5 flex flex-wrap items-center gap-2.5">
            <select aria-label="执行 Agent" value={launchAgent} onChange={(e) => setLaunchAgent(e.target.value)}
              className={SELECT_CLS + ' w-56'}>
              <option value="">执行 Agent：暂不指派</option>
              {agents.map((a) => <option key={a.name} value={a.name}>{a.display_name || a.name}</option>)}
            </select>
            <Button variant="signal" onClick={launch} disabled={launching || !instruction.trim()} className="shrink-0">
              {launching ? '创建中…' : launchAgent ? '指派并创建' : '创建 Task'}
            </Button>
            <span className="font-mono text-[11px] text-ink-3">Ctrl / ⌘ + Enter 提交</span>
          </div>
          <details className="mt-3 border-t border-hairline-soft pt-3">
            <summary className="cursor-pointer select-none text-xs font-medium text-ink-2 hover:text-ink">高级设置</summary>
            <div className="mt-3 flex flex-wrap items-center gap-2.5">
              <select aria-label="实例模型" value={modelProfile}
                onChange={(e) => {
                  setModelProfile(e.target.value)
                  if (!e.target.value) setSubagentModelPolicy('auto')
                }} className={SELECT_CLS + ' w-52'}>
                <option value="">Task 模型：系统默认</option>
                {modelProfiles.map((p) => <option key={p.name} value={p.name}>{p.display_name} · {p.model}</option>)}
              </select>
              <select aria-label="Subagent 模型策略" value={subagentModelPolicy}
                onChange={(e) => setSubagentModelPolicy(e.target.value as 'auto' | 'inherit')}
                disabled={!modelProfile} className={SELECT_CLS + ' w-56'}>
                <option value="auto">Subagent：自动选模</option>
                <option value="inherit">Subagent：继承 Task 模型</option>
              </select>
            </div>
          </details>
        </PanelBody>
      </Panel>

      {err && <ErrorBanner title="Task 加载或创建失败" error={err} className="mb-4" />}

      <Panel className="mb-1.5">
        <div className="grid grid-cols-2 md:grid-cols-4">
          {readouts.map((c, i) => (
            <div key={c.label} className={i < readouts.length - 1 ? 'border-r border-hairline-soft px-5 py-3.5' : 'px-5 py-3.5'}>
              <span className="inline-flex items-center gap-1.5 text-xs font-medium text-ink-2"><Led color={c.color} blink={c.label === '执行中' && c.value > 0} />{c.label}</span>
              <div className={`mt-1 font-mono text-[26px] font-medium tabular-nums ${c.value > 0 && c.label === '异常' ? 'text-led-red' : 'text-ink'}`}>{c.value}</div>
            </div>
          ))}
        </div>
      </Panel>

      <div className="mb-3 mt-5 flex flex-wrap items-center gap-2">
        <span className="panel-title mr-1.5">TASK POOL</span>
        {TAGS.map((tag) => {
          const on = selectedTags.has(tag)
          return (
            <button key={tag} onClick={() => toggleTag(tag)}
              className={`inline-flex items-center gap-1.5 rounded-[2px] border px-3 py-1 font-mono text-[11px] tracking-wide transition-colors ${
                on ? 'border-ink-3 bg-raised text-ink' : 'border-hairline text-ink-3 hover:text-ink-2'
              }`}>
              <Led color={on ? TAG_LED[tag] : 'off'} blink={on && tag === '执行中'} />
              {tag}<span className="text-ink-3">{tasks.filter((t) => tagOf(t) === tag).length}</span>
            </button>
          )
        })}
      </div>

      <div className="space-y-4">
        {forest.map((node) => {
          const st = loopStatus([node])
          const agg = LOOP_AGG[st] ?? { label: st, led: 'off' as const }
          const flat: AgentTask[] = []
          const walk = (n: Node) => { flat.push(n.task); n.children.forEach(walk) }
          walk(node)
          const tokens = flat.reduce((s, t) => s + (t.tokens ?? 0), 0)
          const primary = node.task.pool_status === 'review'
            ? { label: '待验收', led: 'amber' as const }
            : node.task.pool_status === 'done'
              ? { label: node.task.accepted_loop_id ? '已验收' : '已结束', led: node.task.accepted_loop_id ? 'green' as const : 'off' as const }
              : node.task.agent_name === 'unassigned'
                ? { label: '待领取', led: 'amber' as const }
                : agg
          return (
            <Panel key={node.task.id}>
              <PanelHead>
                <div className="flex flex-wrap items-center gap-3.5">
                  <Link to={`/tasks/${node.task.id}`} className="title-link max-w-[620px] truncate text-sm font-semibold tracking-tight"
                    title={node.task.input || ''}>
                    {node.task.input?.trim() || `Task #${node.task.id} · Task 详情`}
                  </Link>
                  <StatusPill color={primary.led} blink={'blink' in primary ? primary.blink : false}>{primary.label}</StatusPill>
                  <span className="font-mono text-[11px] text-ink-3" title={fmtFullTime(node.task.created_at)}>
                    {flat.length} 个 Task · {fmtTokens(tokens)} Token · {fmtTime(node.task.created_at)}
                  </span>
                </div>
                <Link to={`/tasks/${node.task.id}`} className="link-console font-mono text-[11px] text-ink-3">Task #{node.task.id} →</Link>
              </PanelHead>
              <PanelBody>
                <TaskNode node={node} depth={0} root />
                {tagOf(node.task) === '待领取' && <ClaimRow taskId={node.task.id} onChanged={load} />}
                {node.task.pool_status === 'review' && <ReviewPanel taskId={node.task.id} onDone={load} />}
              </PanelBody>
            </Panel>
          )
        })}
        {forest.length === 0 && (
          <Panel><PanelBody className="py-16 text-center font-mono text-sm text-ink-3">当前筛选无 Task。</PanelBody></Panel>
        )}
      </div>
    </div>
  )
}
