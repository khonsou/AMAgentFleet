import { expect, test, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'

const protocol = readFileSync(new URL('../../../fleet-ops-protocol.json', import.meta.url), 'utf-8')
const task = {
  id: 11, input: '生成业务复盘', agent_name: 'commander', status: 'running', pool_status: 'review',
  latest_loop_id: 21, latest_loop_status: 'delivered', latest_thread_id: 'thread-a', latest_deliverable: '复盘初稿',
  latest_message: '正在汇总各业务线结果', result_preview: null, error: null,
  created_at: '2026-10-31T08:00:00Z', updated_at: '2026-10-31T09:00:00Z', parent_task_id: null,
  claimants: [{ loop_id: 21, claimant_type: 'agent', claimant: 'commander', status: 'delivered', thread_id: 'thread-a' }],
}
const conversation = {
  task,
  participants: [{ id: 'agent:commander', name: 'commander', type: 'agent', loop_id: 21, thread_id: 'thread-a', status: 'delivered' }],
  messages: [{ id: 'event:1', kind: 'message', actor: 'commander', content: '我先整理数据', created_at: '2026-10-31T08:01:00Z', loop_id: 21, thread_id: 'thread-a' }],
  outputs: [{ id: 'output:21:v1', loop_id: 21, actor: 'commander', content: '复盘初稿', status: 'delivered', version: 1, created_at: '2026-10-31T08:02:00Z', thread_id: 'thread-a' }],
  children: [],
  loops: [{ id: 21, claimant: 'commander', claimant_type: 'agent', status: 'delivered', thread_id: 'thread-a', deliverable: '复盘初稿', error: null, version: 1 }],
}

function conversationFor99(claimed: boolean) {
  if (!claimed) return { ...conversation, task: { ...task, id: 99, agent_name: 'unassigned', pool_status: 'open', claimants: [] }, participants: [], messages: [], outputs: [], loops: [] }
  const participant = { id: 'agent:researcher', name: 'researcher', type: 'agent', loop_id: 31, thread_id: 'thread-new', status: 'delivered' }
  return {
    ...conversation,
    task: { ...task, id: 99, agent_name: 'researcher', pool_status: 'review', latest_loop_id: 31, latest_loop_status: 'delivered', latest_thread_id: 'thread-new', claimants: [participant] },
    participants: [participant],
    messages: [],
    outputs: [{ id: 'output:31:v1', loop_id: 31, actor: 'researcher', content: '新 Task OUTPUT', status: 'delivered', version: 1, created_at: '2026-10-31T09:02:00Z', thread_id: 'thread-new' }],
    loops: [{ id: 31, claimant: 'researcher', claimant_type: 'agent', status: 'delivered', thread_id: 'thread-new', deliverable: '新 Task OUTPUT', error: null, version: 1 }],
  }
}

async function mockProtocol(page: Page) {
  const writes: Array<Record<string, unknown>> = []
  let agentReads = 0
  let claimed99 = false
  await page.route('**/api/console/operations/protocol', (route) => route.fulfill({ contentType: 'application/json', body: protocol }))
  await page.route('**/api/console/operations/execute', async (route) => {
    const request = route.request().postDataJSON()
    const operation = request.operation_id
    if (request.confirmed) writes.push(request)
    if (operation === 'agents.list') agentReads += 1
    if (operation === 'loops.claim') claimed99 = true
    const result = operation === 'agents.list'
      ? [{ name: 'commander', display_name: 'Commander', status: 'registered', identity: { role: 'Orchestration' } }, { name: 'researcher', display_name: 'Researcher', status: 'registered', identity: { role: 'Research' } }]
      : operation === 'tasks.list' ? [task]
      : operation === 'tasks.conversation' ? (request.path_params.task_id === 99 ? conversationFor99(claimed99) : conversation)
      : operation === 'tasks.create' ? { task_id: 99, pool_status: 'open' }
      : operation === 'threads.create' ? { thread_id: 'agent-thread-1' }
      : operation === 'threads.get' ? { thread: { assistant_id: 'commander' }, runs: [], checkpoints: 0 }
      : operation === 'threads.messages' ? [{ content: { type: 'ai', content: '收到，我会继续处理。' } }]
      : { status: 'ok' }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ operation_id: operation, status: 200, result, truncated: false }) })
  })
  await page.route('**/api/console/threads**', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ thread: {}, runs: [], checkpoints: 0 }) }))
  return Object.assign(writes, { agentReads: () => agentReads })
}

test('真实 3D 场景、Task 群聊与协议写确认', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const writes = await mockProtocol(page)
  await page.goto('/playground')
  await expect(page.locator('#world')).toBeVisible()
  await expect(page.locator('.agent-row')).toHaveCount(2)
  await expect(page.locator('.project-label')).toHaveCount(0)
  await expect(page.locator('.agent-row').first()).toContainText('1 TASK')
  await page.screenshot({ path: 'test-results/playground-desktop.png' })

  await page.locator('.agent-row').first().click()
  await expect(page.locator('#detail-drawer, .detail-drawer')).toContainText('生成业务复盘')
  await page.locator('.drawer-task').filter({ hasText: '生成业务复盘' }).click()
  await expect(page.getByText('TASK GROUP CHAT / #11')).toBeVisible()
  await expect(page.getByText('复盘初稿', { exact: true })).toBeVisible()

  await page.getByLabel('群聊收件人').selectOption('group')
  await page.getByLabel('群聊消息').fill('请补充风险说明')
  await page.getByRole('button', { name: '确认并发送' }).click()
  await expect(page.getByRole('alertdialog')).toBeVisible()
  await expect(page.locator('.confirm-dialog .operation-json')).toHaveCount(0)
  const confirmStyle = await page.getByRole('alertdialog').evaluate((element) => {
    const title = getComputedStyle(element.querySelector('h2'))
    const buttonElements = [...element.querySelectorAll('.drawer-action')]
    const buttons = buttonElements.map((buttonElement) => {
      const style = getComputedStyle(buttonElement)
      return { color: style.color, disabled: buttonElement.disabled, background: style.backgroundColor, opacity: style.opacity, fontSize: style.fontSize, fontFamily: style.fontFamily, height: style.height, borderRadius: style.borderRadius, marginTop: style.marginTop }
    })
    return { titleColor: title.color, titleOpacity: title.opacity, titleFontSize: title.fontSize, buttons }
  })
  expect(confirmStyle.titleColor).toBe('rgb(34, 38, 32)')
  expect(confirmStyle.titleOpacity).toBe('1')
  expect(confirmStyle.titleFontSize).toBe('22px')
  expect(confirmStyle.buttons[0]).toMatchObject({ color: 'rgb(242, 240, 232)', disabled: false, background: 'rgb(52, 59, 50)', fontSize: '9px', height: '38px', borderRadius: '4px', marginTop: '0px' })
  expect(confirmStyle.buttons[1]).toMatchObject({ color: 'rgb(255, 248, 239)', disabled: false, background: 'rgb(239, 91, 42)', fontSize: '9px', height: '38px', borderRadius: '4px', marginTop: '0px' })
  expect(confirmStyle.buttons[1].fontFamily).toContain('IBM Plex Mono')
  await page.screenshot({ path: 'test-results/confirmation-window-repro.png' })
  expect(writes).toHaveLength(0)
  await page.getByRole('button', { name: '确认执行', exact: true }).click()
  await expect(page.getByRole('alertdialog')).toHaveCount(0)
  expect(writes[0].operation_id).toBe('tasks.comment')
  expect(writes[0].confirmed).toBe(true)
  expect(writes[0].idempotency_key).toBeTruthy()

  await page.getByRole('button', { name: '采纳', exact: true }).click()
  await page.getByRole('button', { name: '确认执行', exact: true }).click()
  await expect(page.getByRole('alertdialog')).toHaveCount(0)
  await expect.poll(() => writes.length).toBe(2)
  expect(writes[1].operation_id).toBe('tasks.accept')
  expect(writes[1].body.loop_id).toBe(21)

  await page.getByRole('button', { name: '关闭群聊' }).click()
  await page.getByRole('button', { name: 'OPERATIONS' }).click()
  await page.getByLabel('搜索操作').fill('agents.stop')
  await expect(page.locator('.operation-card')).toHaveCount(1)
  await page.locator('.operation-card').click()
  await expect(page.getByText('agents.stop / control-plane')).toBeVisible()
  expect(errors).toEqual([])
})

test('Overview 滚动、Task Pool 创建与 Agent 直接对话', async ({ page }) => {
  const writes = await mockProtocol(page)
  await page.goto('/playground')
  await expect(page.locator('.agent-row')).toHaveCount(2)
  const overflow = await page.locator('.overview-panel').evaluate((element) => getComputedStyle(element).overflowY)
  expect(overflow).toBe('auto')

  await page.locator('.pool-label').click({ force: true })
  await page.getByLabel('新 Task 指令').fill('验证 Task 创建入口')
  await page.getByLabel('新 Task 执行 Agent').selectOption('commander')
  await page.getByLabel('新 Task 指令').press('Control+Enter')
  await expect(page.getByRole('alertdialog')).toBeVisible()
  await page.getByRole('button', { name: '确认执行', exact: true }).click()
  expect(writes[0].operation_id).toBe('tasks.create')
  expect(writes[0].body.input).toBe('验证 Task 创建入口')
  expect(writes[0].body.assignee).toBe('commander')
  await page.getByRole('button', { name: '关闭群聊' }).click()

  await page.locator('.agent-row').first().click()
  await page.getByRole('tab', { name: 'AGENT CHAT' }).click()
  await page.getByLabel('给 COMMANDER 的消息').fill('请检查当前 Fleet 状态')
  await page.getByLabel('给 COMMANDER 的消息').press('Control+Enter')
  await expect(page.getByRole('alertdialog')).toBeVisible()
  await page.getByRole('button', { name: '确认执行', exact: true }).click()
  expect(writes[1].operation_id).toBe('threads.create')
  expect(writes[1].body.assistant_id).toBe('commander')
  await expect(page.getByText('收到，我会继续处理。')).toBeVisible()
  expect(writes.agentReads()).toBeGreaterThan(1)
})

test('未分配 Task 完成指派、OUTPUT、退回与采纳全流程', async ({ page }) => {
  const writes = await mockProtocol(page)
  await page.goto('/playground')
  await expect(page.locator('.agent-row')).toHaveCount(2)
  await page.locator('.pool-label').click({ force: true })
  await page.getByLabel('新 Task 指令').fill('完成一次标准 Task 流程')
  await page.getByLabel('新 Task 指令').press('Control+Enter')
  await page.getByRole('button', { name: '确认执行', exact: true }).click()

  await expect(page.getByText('ASSIGN AGENT / START EXECUTION')).toBeVisible()
  await page.getByLabel('领取 Task 的 Agent').selectOption('researcher')
  await page.getByRole('button', { name: '检查并指派 Agent' }).click()
  await page.getByRole('button', { name: '确认执行', exact: true }).click()
  await expect(page.getByText('新 Task OUTPUT', { exact: true })).toBeVisible()
  expect(writes[1].operation_id).toBe('loops.claim')
  expect(writes[1].body.claimant).toBe('researcher')

  await page.getByRole('button', { name: '退回', exact: true }).click()
  await page.getByLabel('退回意见').fill('请补充风险数据')
  await page.getByRole('button', { name: '检查并确认退回' }).click()
  await page.getByRole('button', { name: '确认执行', exact: true }).click()
  expect(writes[2].operation_id).toBe('loops.return')
  expect(writes[2].body.comment).toBe('请补充风险数据')
  await expect(page.getByRole('alertdialog')).toHaveCount(0)

  await page.getByRole('button', { name: '采纳', exact: true }).click()
  await expect(page.getByRole('alertdialog')).toBeVisible({ timeout: 15000 })
  await page.getByRole('button', { name: '确认执行', exact: true }).click()
  await expect(page.getByRole('alertdialog')).toHaveCount(0)
  await expect.poll(() => writes.length).toBe(4)
  expect(writes[3].operation_id).toBe('tasks.accept')
  expect(writes[3].body.loop_id).toBe(31)
})

test('Playground 移动视口无横向溢出', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockProtocol(page)
  await page.goto('/playground')
  await expect(page.locator('.agent-row')).toHaveCount(0) // 手机隐藏左侧概览，但场景数据已加载。
  await expect(page.locator('#world')).toBeVisible()
  await page.waitForFunction(() => document.querySelectorAll('.agent-name').length === 2)
  const dimensions = await page.evaluate(() => ({ body: document.body.scrollWidth, viewport: innerWidth }))
  expect(dimensions.body).toBeLessThanOrEqual(dimensions.viewport + 2)
  await page.screenshot({ path: 'test-results/playground-mobile.png' })
})
