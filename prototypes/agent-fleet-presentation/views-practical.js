import { childrenOf, descendantsOf, formatTokens, roots, status, tagOf, tasks } from './data.js'

function pill(task) {
  const meta = status[task.status]
  return `<span class="status-pill ${meta.tone}"><i></i>${meta.label}</span>`
}

function agentLabel(task) {
  return task.agent === 'unassigned' ? '未指派' : task.agent
}

function childRow(task, depth = 1) {
  return `
    <button class="compact-row ${task.status}" style="padding-left:${12 + (depth - 1) * 18}px" type="button" data-task="${task.id}">
      <span class="branch-mark" aria-hidden="true"></span>
      <span class="compact-title"><b>Task #${task.id}</b>${task.title}</span>
      <span class="compact-agent">${agentLabel(task)}</span>
      ${task.loop ? `<span class="loop-label">LOOP #${task.loop}</span>` : '<span class="loop-label empty">无 LOOP</span>'}
      ${pill(task)}
      <span class="row-arrow">↗</span>
    </button>`
}

function compactBranches(task, depth = 1) {
  return childrenOf(task.id).map((child) => childRow(child, depth) + compactBranches(child, depth + 1)).join('')
}

function rootStats(root) {
  const group = [root, ...descendantsOf(root.id)]
  const review = group.filter((task) => task.pool === 'review').length
  const errors = group.filter((task) => task.status === 'error').length
  const running = group.filter((task) => task.status === 'running').length
  return { group, review, errors, running }
}

const lanes = [
  { id: 'queued', title: '待领取', hint: '需要指派' },
  { id: 'running', title: '执行中', hint: 'Agent 正在处理' },
  { id: 'review', title: '待验收', hint: 'OUTPUT 已就绪' },
  { id: 'error', title: '异常', hint: '需要介入' },
  { id: 'done', title: '已结束', hint: '最近完成' },
]

export function renderCompact() {
  return `
    <div class="practical-toolbar">
      <div class="filter-set"><button class="active">全部 <b>${tasks.length}</b></button>${lanes.map((lane) => `<button>${lane.title} <b>${tasks.filter((task) => tagOf(task) === lane.id).length}</b></button>`).join('')}</div>
      <span class="refresh-label">15S REFRESH</span>
    </div>
    <div class="compact-stack">
      ${roots().map((root) => {
        const { group, review, errors, running } = rootStats(root)
        const children = childrenOf(root.id)
        return `
          <article class="compact-card ${errors ? 'has-error' : review ? 'has-review' : ''}">
            <button class="compact-root" type="button" data-task="${root.id}">
              <div class="root-main">
                <span class="root-id">TASK #${root.id}</span>
                <h3>${root.title}</h3>
                <span class="root-agent">${agentLabel(root)}</span>
              </div>
              <div class="root-attention">
                ${errors ? `<span class="attention-text red">${errors} 个异常</span>` : ''}
                ${review ? `<span class="attention-text amber">${review} 个待验收</span>` : ''}
                ${!errors && !review && running ? `<span class="attention-text green">${running} 个执行中</span>` : ''}
                ${!errors && !review && !running ? '<span class="attention-text">状态稳定</span>' : ''}
              </div>
              <div class="root-metrics"><span>${group.length} TASKS</span><span>${formatTokens(group.reduce((sum, task) => sum + task.tokens, 0))} TOKENS</span></div>
              ${pill(root)}
            </button>
            ${children.length ? `<div class="compact-children">${compactBranches(root)}</div>` : ''}
            ${root.pool === 'review' || root.status === 'error' ? `<button class="root-output" data-task="${root.id}" type="button"><span>CURRENT OUTPUT</span><p>${root.output}</p><b>打开对话处理 →</b></button>` : ''}
          </article>`
      }).join('')}
    </div>`
}
