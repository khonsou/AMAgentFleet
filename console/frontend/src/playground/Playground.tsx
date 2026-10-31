import { useCallback, useEffect, useRef, useState } from 'react'
import { createWorld } from './renderers/diorama'
import { toDioramaSnapshot } from './renderers/model'
import { buildSceneViewModel } from './core/model'
import type { AgentView, FleetTask, SceneViewModel } from './core/types'
import { OperationProvider } from './protocol/OperationProvider'
import { useOperations } from './protocol/useOperations'
import TaskConversation from './components/TaskConversation'
import OperationsCenter from './components/OperationsCenter'
import TaskCreateForm from './components/TaskCreateForm'
import AgentConversation from './components/AgentConversation'
import './playground.css'

type Drawer = { type: 'agent' | 'agents' | 'pool' | 'task' | 'operations'; id?: string | number }
const STATUS_LABEL = { working: 'WORKING', waiting: 'NEEDS INPUT', error: 'BLOCKED', idle: 'IDLE', offline: 'OFFLINE' }
const STATUS_COLOR = { working: '#ef5b2a', waiting: '#e7ad4a', error: '#d54c3f', idle: '#7a9c88', offline: '#8d928a' }

function PlaygroundContent() {
  const { execute } = useOperations()
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const labelsRef = useRef<HTMLDivElement>(null)
  const pageRef = useRef<HTMLElement>(null)
  const worldRef = useRef<ReturnType<typeof createWorld> | null>(null)
  const [scene, setScene] = useState<SceneViewModel | null>(null)
  const [drawer, setDrawer] = useState<Drawer | null>(null)
  const [error, setError] = useState('')
  const [live, setLive] = useState(true)
  const [tour, setTour] = useState(true)
  const [clock, setClock] = useState(() => new Date())

  const loadScene = useCallback(async () => {
    try {
      const [agentResponse, taskResponse] = await Promise.all([
        execute<Parameters<typeof buildSceneViewModel>[0]>('agents.list'),
        execute<FleetTask[]>('tasks.list', { query: { limit: 200, offset: 0 } }),
      ])
      setScene(buildSceneViewModel(agentResponse.result, taskResponse.result))
      setError('')
    } catch (reason) { setError(String(reason)) }
  }, [execute])

  useEffect(() => {
    const initial = window.setTimeout(() => void loadScene(), 0)
    const timer = live ? window.setInterval(() => void loadScene(), 15000) : null
    return () => {
      window.clearTimeout(initial)
      if (timer) window.clearInterval(timer)
    }
  }, [live, loadScene])

  useEffect(() => {
    const timer = window.setInterval(() => setClock(new Date()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  const selectObject = useCallback((type: string, id: string) => {
    worldRef.current?.pauseTour()
    if (type === 'agent') {
      setDrawer({ type: 'agent', id })
      worldRef.current?.focus('agent', id)
    } else if (type === 'pool') {
      setDrawer({ type: 'pool' })
      worldRef.current?.focus('pool', 'main')
    }
  }, [])

  useEffect(() => {
    if (!canvasRef.current || !labelsRef.current) return
    const world = createWorld(canvasRef.current, labelsRef.current, { onSelect: selectObject })
    worldRef.current = world
    return () => { world.dispose(); worldRef.current = null }
  }, [selectObject])

  useEffect(() => { if (scene) worldRef.current?.sync(toDioramaSnapshot(scene)) }, [scene])
  useEffect(() => {
    const resize = () => worldRef.current?.resize()
    document.addEventListener('fullscreenchange', resize)
    return () => document.removeEventListener('fullscreenchange', resize)
  }, [])

  const changeView = (view: string) => {
    worldRef.current?.setView(view)
    const captions: Record<string, [string, string, string]> = {
      overview: ['01 / OVERVIEW', 'A living view of your Agent Fleet.', 'Select an Agent’s name to see their Tasks.'],
      agents: ['02 / AGENTS', 'Every name is an entry into their work.', 'Progress bubbles fade in and out; the Agent names stay.'],
      pool: ['03 / TASK POOL', 'Every Task starts here.', 'Select the board to inspect open Tasks.'],
    }
    const caption = captions[view] || captions.overview
    document.querySelector<HTMLElement>('#scene-index')!.textContent = caption[0]
    document.querySelector<HTMLElement>('#scene-title')!.textContent = caption[1]
    document.querySelector<HTMLElement>('#scene-subtitle')!.textContent = caption[2]
    document.querySelectorAll<HTMLElement>('[data-view]').forEach((button) => button.classList.toggle('active', button.dataset.view === view))
  }
  const openTask = (task: FleetTask) => {
    setDrawer({ type: 'task', id: task.id })
    const owner = scene?.agents.find((agent) => agent.tasks.some((item) => item.id === task.id))
    if (owner) worldRef.current?.focus('agent', owner.id)
    else worldRef.current?.focus('pool', 'main')
  }
  const toggleTour = () => {
    const next = !tour
    setTour(next)
    worldRef.current?.setTour(next)
  }
  const selectedAgent = drawer?.type === 'agent' ? scene?.agents.find((agent) => agent.id === drawer.id) : null
  const openTasks = scene?.tasks.filter((task) => task.pool_status !== 'done') || []

  return <main ref={pageRef} id="app" className="playground-page">
    <canvas ref={canvasRef} id="world" aria-label="Agent Fleet 三维游戏场景" />
    <div ref={labelsRef} id="labels" />
    <header className="topbar">
      <div className="brand-mark" aria-hidden="true"><i></i><i></i><i></i></div>
      <div className="brand-copy"><div className="eyebrow">FLEET OPERATIONS / PLAYGROUND 001</div><h1>Agent Fleet <span>/ Playground</span></h1></div>
      <div className="top-status"><span className="demo-chip">SHARED FAP</span><span className="live-chip" style={{ opacity: live ? 1 : .55 }}><b></b> {live ? 'LIVE' : 'PAUSED'}</span><span className="clock">{clock.toLocaleTimeString('zh-CN', { hour12: false })}</span></div>
    </header>

    <aside className="panel overview-panel" aria-label="Fleet 实时概览">
      <div className="panel-heading"><span>FLEET OVERVIEW</span><em>01</em></div>
      <div className="metrics"><div><strong>{String(scene?.agents.length || 0).padStart(2, '0')}</strong><span>AGENTS</span></div><div><strong>{String(scene?.activeAgents || 0).padStart(2, '0')}</strong><span>ACTIVE</span></div><div><strong>{String(scene?.openTasks || 0).padStart(2, '0')}</strong><span>OPEN TASKS</span></div></div>
      <div className="mini-chart" aria-hidden="true"><svg viewBox="0 0 220 54" preserveAspectRatio="none"><path className="chart-fill" d="M0 46L0 37L22 40L44 24L66 31L88 18L110 25L132 12L154 21L176 8L198 15L220 5L220 46Z"/><path className="chart-line" d="M0 37L22 40L44 24L66 31L88 18L110 25L132 12L154 21L176 8L198 15L220 5"/></svg><div><span>ACTIVITY / LIVE MODEL</span><b>{scene ? `${Math.min(99, 40 + scene.openTasks * 4)}%` : '—'}</b></div></div>
      <div className="section-label">ON THE FIELD <span>{scene?.agents.length || 0}</span></div>
      <div className="agent-list">{(scene?.agents || []).map((agent) => <button key={agent.id} className="agent-row" onClick={() => selectObject('agent', agent.id)}><span className="agent-avatar" style={{ '--agent-color': STATUS_COLOR[agent.status] } as React.CSSProperties}></span><span><strong>{agent.name}</strong><small>{agent.tasks.length ? `${agent.tasks.length} TASK${agent.tasks.length > 1 ? 'S' : ''}` : agent.role}</small></span><i className="status-dot" style={{ '--status': STATUS_COLOR[agent.status] } as React.CSSProperties}></i></button>)}</div>
      <button className="text-action" onClick={() => setDrawer({ type: 'agents' })}>VIEW ALL AGENTS <span>↗</span></button>
    </aside>

    <aside className="panel control-panel" aria-label="场景控制">
      <div className="panel-heading"><span>SCENE CONTROL</span><em>02</em></div>
      <div className="section-label">CAMERA</div>
      <div className="segmented"><button data-view="overview" className="active" onClick={() => changeView('overview')}>OVERVIEW</button><button data-view="agents" onClick={() => changeView('agents')}>AGENTS</button><button data-view="pool" onClick={() => changeView('pool')}>TASK POOL</button></div>
      <div className="section-label">DISPLAY LAYERS</div>
      <label className="toggle-row"><span>Progress bubbles</span><input type="checkbox" defaultChecked onChange={(event) => worldRef.current?.setLayer('tasks', event.target.checked)} /><i></i></label>
      <label className="toggle-row"><span>Agent names</span><input type="checkbox" defaultChecked onChange={(event) => worldRef.current?.setLayer('names', event.target.checked)} /><i></i></label>
      <label className="toggle-row"><span>Ambience</span><input type="checkbox" defaultChecked onChange={(event) => worldRef.current?.setLayer('ambience', event.target.checked)} /><i></i></label>
      <div className="control-divider"></div>
      <button className={`tour-button ${tour ? '' : 'off'}`} onClick={toggleTour}><span className="tour-icon">◇</span><span><b>AUTO TOUR</b><small>{tour ? 'ON / HANDS FREE' : 'OFF / MANUAL'}</small></span><i></i></button>
      <div className="camera-hint"><span>DRAG</span> ORBIT <span>SCROLL</span> ZOOM <span>⇧ DRAG</span> PAN</div>
    </aside>

    <section className="scene-caption"><span id="scene-index">01 / OVERVIEW</span><strong id="scene-title">A living view of your Agent Fleet.</strong><p id="scene-subtitle">Select an Agent’s name to see their Tasks.</p></section>
    <footer className="playback-bar">
      <button className="play-button" onClick={() => setLive((value) => !value)} aria-label={live ? '暂停实时刷新' : '恢复实时刷新'}><span>{live ? 'Ⅱ' : '▶'}</span></button>
      <div className="playback-copy"><span>LIVE PROTOCOL</span><strong>{scene ? `Updated ${new Date(scene.generatedAt).toLocaleTimeString('zh-CN', { hour12: false })}` : 'Connecting to Fleet…'}</strong></div>
      <div className="timeline"><i style={{ width: live ? '100%' : '0%' }}></i><b style={{ left: live ? 'calc(100% - 3px)' : '0%' }}></b></div>
      <span className="event-counter">15S REFRESH</span>
      <button className="next-button" onClick={() => setDrawer({ type: 'operations' })}>OPERATIONS <span>→</span></button>
      <button className="icon-button" title="立即刷新" aria-label="立即刷新" onClick={() => void loadScene()}>↺</button>
      <button className="icon-button" title="全屏" aria-label="全屏" onClick={() => { if (document.fullscreenElement) void document.exitFullscreen(); else void pageRef.current?.requestFullscreen() }}>⛶</button>
    </footer>
    <div className="corner-note">SHARED FAP · LIVE DATA<br />DIORAMA 3D · THREE.JS</div>

    {!scene && !error && <div className="playground-loading">正在通过共享协议连接 Fleet…</div>}
    {error && <div className="toast show" role="alert">{error}</div>}
    {drawer && <><div className="drawer-backdrop open" onClick={() => setDrawer(null)}></div><aside className="detail-drawer open" aria-label="Playground 详情" aria-hidden="false">
      {drawer.type === 'task' && <TaskConversation key={Number(drawer.id)} taskId={Number(drawer.id)} agents={scene?.agents || []} onClose={() => setDrawer(null)} onChanged={() => void loadScene()} />}
      {drawer.type === 'operations' && <OperationsCenter onClose={() => setDrawer(null)} />}
      {selectedAgent && <AgentDrawer key={selectedAgent.id} agent={selectedAgent} onClose={() => setDrawer(null)} onTask={openTask} onChanged={() => void loadScene()} />}
      {drawer.type === 'pool' && <PoolDrawer tasks={openTasks} agents={scene?.agents || []} onClose={() => setDrawer(null)} onTask={openTask} onCreated={(taskId) => { void loadScene(); setDrawer({ type: 'task', id: taskId }) }} />}
      {drawer.type === 'agents' && <AgentsDrawer agents={scene?.agents || []} onClose={() => setDrawer(null)} onAgent={(id) => selectObject('agent', id)} />}
    </aside></>}
  </main>
}

function DrawerHead({ kicker, title, subtitle, onClose }: { kicker: string; title: string; subtitle: string; onClose: () => void }) {
  return <><div className="drawer-top"><span className="drawer-kicker">{kicker}</span><button className="drawer-close" onClick={onClose} aria-label="关闭详情">×</button></div><h2>{title}</h2><p className="drawer-subtitle">{subtitle}</p></>
}
function AgentDrawer({ agent, onClose, onTask, onChanged }: { agent: AgentView; onClose: () => void; onTask: (task: FleetTask) => void; onChanged: () => void }) {
  const [tab, setTab] = useState<'tasks' | 'chat'>(agent.id === 'fleet-ops-01' ? 'chat' : 'tasks')
  return <>
    <DrawerHead kicker={`AGENT / ${agent.id}`} title={agent.name} subtitle={agent.role} onClose={onClose} />
    <span className="drawer-status" style={{ '--status': STATUS_COLOR[agent.status] } as React.CSSProperties}><i></i>{STATUS_LABEL[agent.status]}</span>
    <div className="drawer-stats"><div><strong>{String(agent.tasks.length).padStart(2, '0')}</strong><span>OPEN TASKS</span></div><div><strong>{String(agent.tasks.filter((task) => task.latest_deliverable).length).padStart(2, '0')}</strong><span>OUTPUT</span></div><div><strong>{agent.registryStatus === 'destroyed' ? '×' : '✓'}</strong><span>REGISTRY</span></div></div>
    <div className="drawer-tabs" role="tablist"><button role="tab" aria-selected={tab === 'tasks'} className={tab === 'tasks' ? 'active' : ''} onClick={() => setTab('tasks')}>TASKS</button><button role="tab" aria-selected={tab === 'chat'} className={tab === 'chat' ? 'active' : ''} onClick={() => setTab('chat')}>AGENT CHAT</button></div>
    {tab === 'chat' ? <AgentConversation agent={agent} onChanged={onChanged} /> : <section className="drawer-section"><h3>TASKS / SELECT TO OPEN GROUP CHAT</h3>{agent.tasks.map((task) => <button className="drawer-task" key={task.id} onClick={() => onTask(task)}><span><b>{task.input}</b><small>TASK #{task.id} · {task.latest_loop_status || task.status}</small></span><span>↗</span></button>)}{!agent.tasks.length && <div className="empty-state">正在草地休息区待命。</div>}</section>}
  </>
}
function PoolDrawer({ tasks, agents, onClose, onTask, onCreated }: { tasks: FleetTask[]; agents: AgentView[]; onClose: () => void; onTask: (task: FleetTask) => void; onCreated: (taskId: number) => void }) {
  return <>
    <DrawerHead kicker="GLOBAL TASK POOL" title="Task Pool" subtitle="创建新 Task，或点击现有 Task 进入聚合群聊。" onClose={onClose} />
    <div className="drawer-stats"><div><strong>{tasks.length}</strong><span>OPEN</span></div><div><strong>{tasks.filter((task) => task.agent_name === 'unassigned').length}</strong><span>UNCLAIMED</span></div><div><strong>{tasks.filter((task) => task.pool_status === 'review').length}</strong><span>OUTPUT</span></div></div>
    <TaskCreateForm agents={agents} onCreated={onCreated} />
    <section className="drawer-section"><h3>OPEN TASKS</h3>{tasks.map((task) => <button className="drawer-task" key={task.id} onClick={() => onTask(task)}><span><b>{task.input}</b><small>TASK #{task.id} · {task.latest_loop_status || task.status}</small></span><span>↗</span></button>)}</section>
  </>
}
function AgentsDrawer({ agents, onClose, onAgent }: { agents: AgentView[]; onClose: () => void; onAgent: (id: string) => void }) {
  return <><DrawerHead kicker="FIELD REGISTRY" title="All Agents" subtitle="选择一个 Agent 聚焦其位置并查看全部 Task。" onClose={onClose} />{agents.map((agent) => <button className="drawer-task" key={agent.id} onClick={() => onAgent(agent.id)}><span><b>{agent.name}</b><small>{agent.role} · {STATUS_LABEL[agent.status]}</small></span><span>↗</span></button>)}</>
}

export default function Playground() {
  return <OperationProvider><PlaygroundContent /></OperationProvider>
}
