import { agents, childrenOf, formatTokens, status, taskById, tasks } from './data.js'
import { addMessage, dummyReply, renderConversation } from './conversation.js'
import { defaultLoop, loopsFor, loopStatus } from './execution.js'
import { renderCompact } from './views-practical.js'

const option = {
  id: 'compact', label: '紧凑列表', index: 'A1', title: '现有页面的减法，而不是换一种装饰。',
  description: '主结构是父子 Task；每个节点默认只有一个 LOOP 和对应的 Thread 对话。',
  question: '能否少读文字，仍然知道下一步？', tags: ['操作效率', '现有页面'],
  render: renderCompact,
}

const state = { live: true, tick: 0 }
const viewHeading = document.querySelector('#view-heading')
const view = document.querySelector('#view')
const drawer = document.querySelector('#detail-drawer')
const backdrop = document.querySelector('#drawer-backdrop')
const toast = document.querySelector('#toast')
const baseTokens = tasks.reduce((total, task) => total + loopsFor(task).reduce((sum, loop) => sum + loop.tokens, 0), 0)

function renderSummary() {
  document.querySelector('#agent-count').textContent = agents.length
  document.querySelector('#task-count').textContent = tasks.length
  document.querySelector('#attention-count').textContent = tasks.filter((task) => task.pool === 'review' || task.status === 'error').length
  document.querySelector('#token-total').textContent = formatTokens(baseTokens + state.tick * 37)
}

function render() {
  renderSummary()
  viewHeading.innerHTML = `
    <div class="view-title"><span>${option.index} / INFORMATION EFFICIENCY</span><h2>${option.title}</h2><p>${option.description}</p></div>
    <div class="view-question"><span>核心问题</span><strong>${option.question}</strong><div>${option.tags.map((tag) => `<i>${tag}</i>`).join('')}</div></div>`
  view.className = `viewport practical view-${option.id}`
  view.innerHTML = option.render()
  document.title = `${option.label} · Agent Fleet 表现层原型`
}

let activeTaskId = null
const pendingLoops = new Set()
const selectedLoops = new Map()

function activeLoop(task) {
  const loops = loopsFor(task)
  return loops.find((loop) => loop.id === selectedLoops.get(task.id)) || defaultLoop(task)
}

function renderExecutionContext(task, current) {
  if (!current) return '<div class="execution-context empty"><span>EXECUTION</span><p>尚未领取，尚无 LOOP 与 Thread</p></div>'
  return `<div class="execution-context">
    <span>CURRENT EXECUTION</span><b>LOOP #${current.id}</b><i>v${current.version} · ${current.runs} RUNS</i><code>Thread ${current.thread}</code>
  </div>`
}

function renderCompetingLoops(task, current) {
  const loops = loopsFor(task)
  if (loops.length < 2) return ''
  return `<details class="competing-loops"><summary>竞争执行 · ${loops.length} 个 LOOP <span>不同认领者 · 默认折叠</span></summary>
    <div>${loops.map((loop) => {
      const meta = loopStatus[loop.status]
      return `<button type="button" data-loop-task="${task.id}" data-loop-id="${loop.id}" class="loop-option ${meta.tone} ${current?.id === loop.id ? 'active' : ''}"><b>LOOP #${loop.id}</b><small>${loop.agent} · ${meta.label} · v${loop.version}</small><em>Thread ${loop.thread}</em></button>`
    }).join('')}</div><p>只有不同 claimant 才会形成额外 LOOP；退回与重试不会出现在这里。</p>
  </details>`
}

function renderLineage(task) {
  const parent = task.parent ? taskById(task.parent) : null
  const children = childrenOf(task.id)
  if (!parent && !children.length) return ''
  return `<nav class="drawer-lineage" aria-label="父子 Task 导航"><span>父子 TASK</span><div>
    ${parent ? `<button type="button" data-task="${parent.id}">↑ #${parent.id} ${parent.title}</button>` : ''}
    ${children.map((child) => `<button type="button" data-task="${child.id}">↓ #${child.id} ${child.title}</button>`).join('')}
  </div></nav>`
}

function renderTaskDrawer(task) {
  const meta = status[task.status]
  const loop = activeLoop(task)
  const loops = loopsFor(task)
  drawer.innerHTML = `
    <header class="drawer-header"><div><span>TASK / #${task.id}</span><h2>${task.title}</h2></div><button type="button" class="drawer-close" aria-label="关闭 Task 工作区">×</button></header>
    <div class="drawer-status"><span class="status-pill ${meta.tone}"><i></i>${meta.label}</span><span>${loops.length > 1 ? `${loops.length} 个竞争 LOOP` : `${loops.length} 个 LOOP`} · ${task.updated}更新</span></div>
    ${renderLineage(task)}
    ${renderExecutionContext(task, loop)}
    ${renderCompetingLoops(task, loop)}
    <div class="drawer-workspace">
      ${renderConversation(task, loop, loop ? pendingLoops.has(loop.id) : false)}
      <aside class="task-context" aria-label="选中 LOOP 的上下文">
        <dl class="detail-grid">
          <div><dt>AGENT</dt><dd>${loop?.agent || '待分配'}</dd></div>
          <div><dt>MODEL</dt><dd>${loop?.model || '—'}</dd></div>
          <div><dt>LOOP</dt><dd>${loop ? `#${loop.id} · v${loop.version}` : '尚未产生'}</dd></div>
          <div><dt>THREAD</dt><dd>${loop?.thread || '等待启动'}</dd></div>
          <div><dt>RUNS</dt><dd>${loop?.runs || 0}</dd></div>
          <div><dt>TOKENS</dt><dd>${formatTokens(loop?.tokens || 0)}</dd></div>
          <div><dt>PARENT</dt><dd>${task.parent ? `Task #${task.parent}` : '根 Task'}</dd></div>
          <div><dt>MODEL POLICY</dt><dd>${task.modelPolicy || 'auto'}</dd></div>
        </dl>
        ${loop?.error ? `<section class="drawer-error"><span>ERROR / LOOP #${loop.id}</span><p>${loop.error}</p></section>` : ''}
        <section class="drawer-output"><span>${loop ? `${loop.id === task.acceptedLoop ? 'FINAL' : 'CURRENT'} OUTPUT · LOOP #${loop.id}` : 'OUTPUT · 等待 LOOP'}</span><p>${loop?.output || '尚未产生 OUTPUT。'}</p></section>
        <details class="json-fold"><summary>LOOP 原始 JSON <span>默认折叠</span></summary><pre>{
  "task_id": ${task.id},
  "loop_id": ${loop?.id || 'null'},
  "thread_id": "${loop?.thread || ''}",
  "status": "${loop?.status || 'unclaimed'}",
  "version": ${loop?.version || 0},
  "runs": ${loop?.runs || 0},
  "tokens": ${loop?.tokens || 0}
}</pre></details>
        ${loop ? `<div class="drawer-actions"><button type="button" data-proto-action="thread">完整 Thread</button>${task.pool === 'review' && loop.status === 'delivered' ? '<button type="button" class="primary" data-proto-action="accept">采纳 OUTPUT</button>' : loop.status === 'error' ? '<button type="button" class="primary" data-proto-action="retry">重试 LOOP</button>' : ''}</div>` : ''}
      </aside>
    </div>
    <p class="drawer-proto-note">默认 1 Task → 1 LOOP → 1 Thread；退回与重试复用原 LOOP。只有不同认领者竞争时才出现额外 LOOP。</p>`
  requestAnimationFrame(() => {
    const list = drawer.querySelector('#task-message-list')
    if (list) list.scrollTop = list.scrollHeight
  })
}

function openTask(id, loopId = null) {
  const task = taskById(id)
  if (!task) return
  if (loopId && loopsFor(task).some((loop) => loop.id === loopId)) selectedLoops.set(task.id, loopId)
  activeTaskId = task.id
  renderTaskDrawer(task)
  drawer.hidden = false
  backdrop.hidden = false
  requestAnimationFrame(() => drawer.classList.add('open'))
}

function closeDrawer() {
  activeTaskId = null
  drawer.classList.remove('open')
  drawer.hidden = true
  backdrop.hidden = true
}

let toastTimer
function showToast(message) {
  clearTimeout(toastTimer)
  toast.textContent = message
  toast.hidden = false
  toastTimer = setTimeout(() => { toast.hidden = true }, 2400)
}

function sendInstruction(task, loop, instruction) {
  if (!instruction || pendingLoops.has(loop.id)) return
  addMessage(loop.id, 'human', instruction)
  pendingLoops.add(loop.id)
  renderTaskDrawer(task)

  setTimeout(() => {
    addMessage(loop.id, 'agent', dummyReply(task, loop, instruction))
    pendingLoops.delete(loop.id)
    if (activeTaskId === task.id && activeLoop(task)?.id === loop.id) renderTaskDrawer(task)
  }, 450)
}

document.addEventListener('submit', (event) => {
  const form = event.target.closest('.message-composer[data-task-id]')
  if (!form) return
  event.preventDefault()
  const task = taskById(form.dataset.taskId)
  const loopId = Number(form.dataset.loopId)
  const loop = task && loopsFor(task).find((item) => item.id === loopId)
  const input = form.querySelector('[data-message-input]')
  const instruction = input.value.trim()
  if (!task || !loop || !instruction) return
  sendInstruction(task, loop, instruction)
})

document.addEventListener('click', (event) => {
  const loopTarget = event.target.closest('[data-loop-open]')
  if (loopTarget) {
    openTask(loopTarget.dataset.task, Number(loopTarget.dataset.loopOpen))
    return
  }
  const loopButton = event.target.closest('button[data-loop-id]')
  if (loopButton) {
    const task = taskById(loopButton.dataset.loopTask)
    selectedLoops.set(task.id, Number(loopButton.dataset.loopId))
    renderTaskDrawer(task)
    return
  }
  const task = event.target.closest('[data-task]')
  if (task) {
    openTask(task.dataset.task)
    return
  }
  if (event.target.closest('.drawer-close') || event.target === backdrop) {
    closeDrawer()
    return
  }
  const action = event.target.closest('[data-proto-action]')
  if (action) {
    showToast('原型操作：仅展示交互位置，不发送请求')
    return
  }
  if (event.target.closest('.filter-set button')) {
    event.target.closest('.filter-set').querySelectorAll('button').forEach((button) => button.classList.remove('active'))
    event.target.closest('button').classList.add('active')
    showToast('筛选交互已标示；dummy data 保持完整以便横向比较')
    return
  }
  if (event.target.closest('button') && !event.target.closest('#live-toggle') && !event.target.closest('.message-composer')) {
    showToast('此控件用于表现层评估，未接入真实功能')
  }
})

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeDrawer()
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && event.target.matches('[data-message-input]')) {
    event.preventDefault()
    event.target.form.requestSubmit()
  }
  if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('[data-task][role="button"]')) {
    event.preventDefault()
    openTask(event.target.dataset.task)
  }
})

document.querySelector('#live-toggle').addEventListener('click', () => {
  state.live = !state.live
  document.body.classList.toggle('paused', !state.live)
  document.querySelector('#live-toggle').setAttribute('aria-pressed', String(state.live))
  document.querySelector('#live-label').textContent = state.live ? 'LIVE' : 'PAUSED'
})

setInterval(() => {
  if (!state.live) return
  state.tick += 1
  renderSummary()
}, 1800)

function syncFromHash() {
  if (location.hash !== '#compact') history.replaceState(null, '', '#compact')
  closeDrawer()
  render()
}

window.addEventListener('hashchange', syncFromHash)
syncFromHash()
