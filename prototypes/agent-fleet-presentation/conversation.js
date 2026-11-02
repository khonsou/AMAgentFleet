const conversations = new Map()

const escapeHtml = (value) => String(value)
  .replaceAll('&', '&amp;')
  .replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;')
  .replaceAll("'", '&#039;')

function seedMessages(task, loop) {
  const response = loop.status === 'error'
    ? `检查在字段 renewal_rate 处中断。${loop.error}`
    : loop.status === 'delivered'
      ? `已在 LOOP #${loop.id} 提交 OUTPUT。你可以继续补充要求，或结合右侧 OUTPUT 验收。${loop.output}`
      : loop.status === 'accepted' || loop.status === 'success'
        ? `本次 Agent 实例已结束。${loop.output}`
        : `LOOP #${loop.id} 正在执行。${loop.output}`

  const messages = [
    { role: 'human', content: `请执行：${task.title}。保留关键依据，遇到异常及时说明。`, time: '10:24' },
    { role: 'agent', content: response, time: loop.updated, tool: true },
  ]

  // 退回只增加同一 LOOP 的 version 和同一 Thread 的消息，不创建历史 LOOP。
  if (loop.version > 1) {
    messages.splice(1, 0,
      { role: 'agent', content: '初版已提交，但销售数据口径与风险复核仍需补充。', time: '10:31', tool: true },
      { role: 'human', content: '退回补充：请统一销售数据口径，并等待风险复核。', time: '10:36' },
      { role: 'agent', content: `已在同一 LOOP #${loop.id}、同一 Thread ${loop.thread} 中继续执行；当前为 v${loop.version}。`, time: '10:37', tool: true },
    )
  }
  return messages
}

export function messagesFor(task, loop) {
  if (!conversations.has(loop.id)) conversations.set(loop.id, seedMessages(task, loop))
  return conversations.get(loop.id)
}

export function addMessage(loopId, role, content) {
  const messages = conversations.get(loopId) || []
  messages.push({ role, content, time: '刚刚' })
  conversations.set(loopId, messages)
}

function toolFold(loop) {
  return `<details class="message-tool"><summary>Tool 调用 2 次 <span>JSON 默认折叠</span></summary><pre>{
  "loop_id": ${loop.id},
  "thread_id": "${escapeHtml(loop.thread)}",
  "tool_calls": [
    { "name": "read_data", "status": "success", "duration_ms": 842 },
    { "name": "aggregate", "status": "${loop.status === 'error' ? 'error' : 'success'}", "duration_ms": 1280 }
  ]
}</pre></details>`
}

function nativeSubagentFold(task, loop) {
  if (!task.nativeSubagents?.length) return ''
  return `<details class="native-subagent-fold"><summary>原生 task · ${task.nativeSubagents.length} 个 subagent <span>同一 Thread · 非 Fleet Task</span></summary>
    <div>${task.nativeSubagents.map((subagent) => `<section><header><b>${escapeHtml(subagent.type)}</b><span>${escapeHtml(subagent.status)}</span></header><p>${escapeHtml(subagent.description)}</p><code>thread_id: ${escapeHtml(loop.thread)}</code><small>Thread 内部委托执行；不创建 Fleet Task、LOOP 或新业务 Thread。</small></section>`).join('')}</div>
  </details>`
}

function renderMessage(task, message, loop) {
  const human = message.role === 'human'
  return `<article class="chat-message ${human ? 'human' : 'agent'}">
    <header><strong>${human ? '你' : escapeHtml(loop.agent)}</strong><span>${escapeHtml(message.time)}</span></header>
    <p>${escapeHtml(message.content)}</p>
    ${message.tool ? toolFold(loop) : ''}
  </article>`
}

export function renderConversation(task, loop, pending = false, idPrefix = 'task') {
  const inputId = `${idPrefix}-message-input`
  if (!loop) {
    return `<section class="conversation-panel no-instance" aria-label="等待 Agent 实例">
      <header class="conversation-head"><div><span>AGENT INSTANCE</span><strong>尚未产生 Agent 实例</strong></div><b>NO THREAD</b></header>
      <div class="empty-instance"><span>TASK #${task.id}</span><h3>${escapeHtml(task.title)}</h3><p>Task 已创建，但还没有 LOOP。领取后才会启动 Agent 实例与 Thread，届时才能向执行中的 Agent 发送指令。</p><button type="button" data-proto-action="claim">选择领取者</button></div>
    </section>`
  }

  const messages = messagesFor(task, loop)
  return `<section class="conversation-panel" aria-label="Agent 实例对话">
    <header class="conversation-head">
      <div><span>AGENT INSTANCE / LOOP #${loop.id}</span><strong>${escapeHtml(loop.agent)} · ${escapeHtml(loop.model)}</strong></div>
      <b>THREAD ${escapeHtml(loop.thread)}</b>
    </header>
    <div class="message-list" id="${idPrefix}-message-list" aria-live="polite">
      ${messages.map((message) => renderMessage(task, message, loop)).join('')}
      ${nativeSubagentFold(task, loop)}
      ${pending ? '<div class="agent-pending"><i></i>当前 Agent 实例正在处理…</div>' : ''}
    </div>
    <form class="message-composer" id="${idPrefix}-message-form" data-task-id="${task.id}" data-loop-id="${loop.id}">
      <label for="${inputId}">向 LOOP #${loop.id} 的 ${escapeHtml(loop.agent)} 实例发送指令</label>
      <textarea id="${inputId}" data-message-input rows="3" placeholder="补充要求、纠正方向或追问当前结果…（Ctrl/⌘ + Enter 发送）" ${pending ? 'disabled' : ''}></textarea>
      <footer><span>消息属于当前 Thread · 本地 dummy 回复 · 不调用模型</span><button type="submit" ${pending ? 'disabled' : ''}>${pending ? '处理中…' : '发送'}</button></footer>
    </form>
  </section>`
}

export function dummyReply(task, loop, instruction) {
  return `已收到补充指令：“${instruction}”。我会在 Task #${task.id} → LOOP #${loop.id} → Thread ${loop.thread} 的上下文中继续处理。此回复仅用于验证实例级对话。`
}
