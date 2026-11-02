import './style.css'
import { agents, demoEvents, initialSnapshot } from './data.js'
import { createWorld } from './world.js'

const $ = (selector) => document.querySelector(selector)
const statusMeta = {
  working: { label: 'WORKING', color: '#ef5b2a' },
  waiting: { label: 'NEEDS INPUT', color: '#e7ad4a' },
  error: { label: 'BLOCKED', color: '#d54c3f' },
  delivered: { label: 'OUTPUT READY', color: '#ef5b2a' },
  done: { label: 'ACCEPTED', color: '#7a9c88' },
  queued: { label: 'UNCLAIMED', color: '#8d928a' },
  idle: { label: 'IDLE', color: '#7a9c88' },
}
let state = initialSnapshot()
let playing = true
let tour = true
let eventTimer = null
let toastTimer = null
let interactionTimer = null
let selected = null

const world = createWorld($('#world'), $('#labels'), {
  onSelect: selectObject,
  onInteract: pauseForInteraction,
})

function showToast(message) {
  clearTimeout(toastTimer)
  $('#toast').textContent = message
  $('#toast').classList.add('show')
  toastTimer = setTimeout(() => $('#toast').classList.remove('show'), 3400)
}
function pauseForInteraction() {
  world.pauseTour()
  if (tour) $('#tour-state').textContent = 'PAUSED / RESUMES SOON'
  clearTimeout(interactionTimer)
  interactionTimer = setTimeout(() => {
    if (tour) {
      world.resumeTour()
      $('#tour-state').textContent = 'ON / HANDS FREE'
    }
  }, 12000)
}
function renderAgents() {
  $('#agent-list').innerHTML = state.agents.map((agent) => {
    const count = state.tasks.filter((task) => task.agent === agent.id && task.status !== 'done').length
    return `<button class="agent-row" data-agent="${agent.id}"><span class="agent-avatar" style="--agent-color:${agent.color}"></span><span><strong>${agent.name}</strong><small>${count ? `${count} TASK${count > 1 ? 'S' : ''}` : agent.role}</small></span><i class="status-dot" style="--status:${statusMeta[agent.status].color}"></i></button>`
  }).join('')
  $('#agent-list').querySelectorAll('[data-agent]').forEach((button) => {
    button.addEventListener('click', () => selectObject('agent', button.dataset.agent))
  })
  $('#metric-active').textContent = String(state.agents.filter((agent) => agent.status === 'working').length).padStart(2, '0')
  $('#metric-projects').textContent = String(state.tasks.filter((task) => task.status !== 'done').length).padStart(2, '0')
  $('#activity-value').textContent = `${Math.min(96, 58 + state.eventIndex * 4)}%`
}
function renderPlayback() {
  const progress = (state.eventIndex / demoEvents.length) * 100
  $('#timeline-progress').style.width = `${progress}%`
  $('#timeline-dot').style.left = `calc(${progress}% - 3px)`
  $('#event-counter').textContent = `EVENT ${String(state.eventIndex).padStart(2, '0')} / ${String(demoEvents.length).padStart(2, '0')}`
  $('#event-label').textContent = state.eventIndex ? demoEvents[state.eventIndex - 1].label : 'Fleet is running'
  $('#next-event').disabled = state.eventIndex >= demoEvents.length
  $('#next-event').innerHTML = state.eventIndex >= demoEvents.length ? 'DEMO COMPLETE' : 'NEXT EVENT <span>→</span>'
  if (selected) renderDrawer(selected.type, selected.id, false)
}
function sync() {
  world.sync(state)
  renderAgents()
  renderPlayback()
}
function applyEvent(event) {
  const task = state.tasks.find((item) => item.id === event.task)
  if (task) {
    if (event.agent) task.agent = event.agent
    if (event.status) task.status = event.status
    if (event.short) task.short = event.short
    if (event.progress !== undefined) task.progress = event.progress
    if (event.output) task.output = event.output
    task.messages += 1
    task.updated = state.eventIndex + 1
    const agent = state.agents.find((item) => item.id === event.agent)
    if (agent) agent.status = event.status === 'done' ? 'idle' : event.status === 'delivered' ? 'waiting' : 'working'
  }
  state.eventIndex += 1
  world.playEvent(event)
  sync()
  showToast(event.toast)
}
function nextEvent(manual = true) {
  if (state.eventIndex >= demoEvents.length) {
    if (manual) showToast('演示事件已全部完成，可以重置后再次播放。')
    setPlaying(false)
    return
  }
  applyEvent(demoEvents[state.eventIndex])
  scheduleNext()
}
function scheduleNext() {
  clearTimeout(eventTimer)
  if (playing && state.eventIndex < demoEvents.length) eventTimer = setTimeout(() => nextEvent(false), 8500)
}
function setPlaying(value) {
  playing = value
  $('#play-icon').textContent = playing ? 'Ⅱ' : '▶'
  $('#play-toggle').setAttribute('aria-label', playing ? '暂停演示' : '播放演示')
  $('.live-chip').style.opacity = playing ? '1' : '.55'
  scheduleNext()
}
function resetDemo() {
  state = initialSnapshot()
  selected = null
  closeDrawer()
  world.setView('overview')
  setViewUI('overview')
  sync()
  setPlaying(true)
  showToast('已恢复初始 demo data。')
}
function setViewUI(view) {
  const captions = {
    overview: ['01 / OVERVIEW', 'A living view of your Agent Fleet.', 'Select an Agent’s name to see their Tasks.'],
    agents: ['02 / AGENTS', 'Every name is an entry into their work.', 'Progress bubbles fade in and out; the Agent names stay.'],
    pool: ['03 / TASK POOL', 'Every Task starts here.', 'Select the board to inspect open Tasks.'],
  }
  $('#scene-index').textContent = captions[view][0]
  $('#scene-title').textContent = captions[view][1]
  $('#scene-subtitle').textContent = captions[view][2]
  document.querySelectorAll('[data-view]').forEach((button) => button.classList.toggle('active', button.dataset.view === view))
}
function changeView(view) {
  pauseForInteraction()
  world.setView(view)
  setViewUI(view)
}
function selectObject(type, id) {
  pauseForInteraction()
  selected = { type, id }
  renderDrawer(type, id, true)
  if (type === 'task') {
    const task = state.tasks.find((item) => item.id === id)
    if (task?.agent) world.focus('agent', task.agent)
    else world.focus('pool')
  } else world.focus(type, id)
}
function drawerHeader(kicker, title, subtitle, status) {
  return `<div class="drawer-top"><span class="drawer-kicker">${kicker}</span><button class="drawer-close" aria-label="关闭详情">×</button></div><h2>${title}</h2><p class="drawer-subtitle">${subtitle}</p>${status ? `<span class="drawer-status" style="--status:${statusMeta[status].color}"><i></i>${statusMeta[status].label}</span>` : ''}`
}
function bindDrawer() {
  $('#detail-drawer').setAttribute('aria-hidden', 'false')
  $('#detail-drawer').classList.add('open')
  $('#drawer-backdrop').classList.add('open')
  $('.drawer-close').onclick = closeDrawer
  $('#drawer-backdrop').onclick = closeDrawer
  $('#detail-drawer').querySelectorAll('[data-open-task]').forEach((button) => { button.onclick = () => selectObject('task', button.dataset.openTask) })
  $('#detail-drawer').querySelectorAll('[data-open-agent]').forEach((button) => { button.onclick = () => selectObject('agent', button.dataset.openAgent) })
  $('#detail-drawer').querySelectorAll('[data-open-project]').forEach((button) => { button.onclick = () => selectObject('project', button.dataset.openProject) })
  $('[data-drawer-next]')?.addEventListener('click', () => nextEvent())
}
function renderDrawer(type, id, open = true) {
  const drawer = $('#detail-drawer')
  if (type === 'task') {
    const task = state.tasks.find((item) => item.id === id)
    if (!task) return closeDrawer()
    const agent = state.agents.find((item) => item.id === task.agent)
    drawer.innerHTML = `${drawerHeader(`TASK / ${task.id}`, task.title, task.detail, task.status)}<div class="drawer-stats"><div><strong>${task.progress}%</strong><span>PROGRESS</span></div><div><strong>${task.messages}</strong><span>MESSAGES</span></div><div><strong>${task.output ? '01' : '00'}</strong><span>OUTPUT</span></div></div><section class="drawer-section"><h3>GROUP CHAT / PARTICIPANTS</h3>${agent ? `<button class="drawer-task" data-open-agent="${agent.id}"><span><b>${agent.name}</b><small>主 Agent · ${agent.role}</small></span><span>↗</span></button><div class="chat-message"><b>${agent.name}</b>${task.short}。需要时会在当前群聊中编排 subagent。</div><div class="chat-message"><b>YOU</b>可以在同一个 Task 群聊中指导、验收或退回。</div>` : '<div class="empty-state">等待 Agent 从 Task Pool 领取。</div>'}${task.output ? `<div class="chat-message output"><b>OUTPUT</b>${task.output}</div>` : ''}</section><button class="drawer-action" data-drawer-next>推进下一个演示事件 →</button>`
  } else if (type === 'agent') {
    const agent = state.agents.find((item) => item.id === id)
    if (!agent) return closeDrawer()
    const agentTasks = state.tasks.filter((task) => task.agent === id && task.status !== 'done')
    drawer.innerHTML = `${drawerHeader(`AGENT / ${agent.id.toUpperCase()}`, agent.name, agent.role, agent.status)}<div class="drawer-stats"><div><strong>${String(agentTasks.length).padStart(2, '0')}</strong><span>OPEN TASKS</span></div><div><strong>${String(agentTasks.filter((task) => task.output).length).padStart(2, '0')}</strong><span>OUTPUT</span></div><div><strong>${agent.status === 'error' ? '!' : '✓'}</strong><span>FIELD STATE</span></div></div><section class="drawer-section"><h3>TASKS / SELECT TO OPEN GROUP CHAT</h3>${agentTasks.length ? agentTasks.map((task) => `<button class="drawer-task" data-open-task="${task.id}"><span><b>${task.title}</b><small>${task.id} · ${task.short}</small></span><span>↗</span></button>`).join('') : '<div class="empty-state">正在草地休息区待命。</div>'}</section><button class="drawer-action" data-drawer-next>推进下一个演示事件 →</button>`
  } else if (type === 'pool') {
    const openTasks = state.tasks.filter((task) => task.status !== 'done')
    drawer.innerHTML = `${drawerHeader('GLOBAL TASK POOL', 'Task Pool', '所有 Task 都从这里开始；点击任意 Task 进入群聊。', 'working')}<div class="drawer-stats"><div><strong>${openTasks.length}</strong><span>OPEN</span></div><div><strong>${openTasks.filter((task) => !task.agent).length}</strong><span>UNCLAIMED</span></div><div><strong>${openTasks.filter((task) => task.status === 'delivered').length}</strong><span>OUTPUT</span></div></div><section class="drawer-section pool-list"><h3>LIVE QUEUE</h3>${openTasks.map((task) => `<button class="drawer-task" data-open-task="${task.id}"><span><b>${task.title}</b><small>${task.id} · ${task.short}</small></span><span>↗</span></button>`).join('')}</section><button class="drawer-action" data-drawer-next>推进下一个演示事件 →</button>`
  } else if (type === 'agents') {
    drawer.innerHTML = `${drawerHeader('FIELD REGISTRY', 'All Agents', '选择一个 Agent 聚焦其位置并查看所有 Task 气泡。', null)}${state.agents.map((agent) => `<button class="drawer-task" data-open-agent="${agent.id}"><span><b>${agent.name}</b><small>${agent.role} · ${statusMeta[agent.status].label}</small></span><span>↗</span></button>`).join('')}`
  }
  if (open) bindDrawer()
}
function closeDrawer() {
  selected = null
  $('#detail-drawer').classList.remove('open')
  $('#detail-drawer').setAttribute('aria-hidden', 'true')
  $('#drawer-backdrop').classList.remove('open')
}

$('#play-toggle').addEventListener('click', () => setPlaying(!playing))
$('#next-event').addEventListener('click', () => nextEvent())
$('#reset-demo').addEventListener('click', resetDemo)
$('#show-all-agents').addEventListener('click', () => selectObject('agents', 'all'))
$('#tour-toggle').addEventListener('click', () => {
  tour = !tour
  world.setTour(tour)
  $('#tour-toggle').classList.toggle('off', !tour)
  $('#tour-state').textContent = tour ? 'ON / HANDS FREE' : 'OFF / MANUAL'
})
document.querySelectorAll('[data-view]').forEach((button) => button.addEventListener('click', () => changeView(button.dataset.view)))
;[['#toggle-tasks', 'tasks'], ['#toggle-links', 'names'], ['#toggle-ambience', 'ambience']].forEach(([selector, layer]) => {
  $(selector).addEventListener('change', (event) => world.setLayer(layer, event.target.checked))
})
$('#fullscreen').addEventListener('click', async () => {
  try {
    if (document.fullscreenElement) await document.exitFullscreen()
    else await $('#app').requestFullscreen()
  } catch { showToast('当前浏览器不支持全屏，可继续缩放场景。') }
})
document.addEventListener('fullscreenchange', () => setTimeout(world.resize, 100))
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeDrawer()
  if (event.code === 'Space') { event.preventDefault(); setPlaying(!playing) }
  if (event.key.toLowerCase() === 'n') nextEvent()
  if (event.key.toLowerCase() === 't') $('#tour-toggle').click()
  if (['1', '2', '3'].includes(event.key)) changeView({ 1: 'overview', 2: 'agents', 3: 'pool' }[event.key])
})

let clockSeconds = 11 * 3600 + 9 * 60
setInterval(() => {
  if (!playing) return
  clockSeconds += 1
  const h = String(Math.floor(clockSeconds / 3600) % 24).padStart(2, '0')
  const m = String(Math.floor(clockSeconds / 60) % 60).padStart(2, '0')
  const s = String(clockSeconds % 60).padStart(2, '0')
  $('#clock').textContent = `${h}:${m}:${s}`
}, 1000)

sync()
setPlaying(true)
window.__fleetDemo = {
  get state() { return state },
  get playing() { return playing },
  get tour() { return tour },
  select: selectObject,
  nextEvent,
  reset: resetDemo,
  setView: changeView,
  stats: () => world.getStats(),
  screenPoint: (type, id) => world.projectSelectable(type, id),
}
