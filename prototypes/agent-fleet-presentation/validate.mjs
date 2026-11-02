import { mkdir } from 'node:fs/promises'
import { chromium } from '../../console/frontend/node_modules/playwright/index.mjs'
import { startServer } from './server.mjs'
import { taskById, tasks } from './data.js'
import { defaultLoop, loopsFor } from './execution.js'

const retiredViews = ['map', 'kanban', 'topology', 'timeline', 'flow', 'command', 'office']
const artifacts = new URL('./artifacts/', import.meta.url).pathname
await mkdir(artifacts, { recursive: true })

// 先验证 dummy data 本身，避免浏览器测试通过但业务语义错误。
const root = taskById(101)
const rootLoops = loopsFor(root)
if (rootLoops.length !== 1 || rootLoops[0].id !== 12 || rootLoops.some((loop) => loop.id === 11)) {
  throw new Error('Task #101 must have exactly one LOOP #12; same-claimant history is forbidden')
}
if (rootLoops[0].thread !== 'c41f9a2e' || rootLoops[0].version !== 2 || rootLoops[0].runs !== 3) {
  throw new Error('Task #101 return/retry must reuse LOOP #12 and its Thread')
}
if (defaultLoop(root)?.id !== 12) throw new Error('Task #101 default LOOP changed')
const competition = loopsFor(taskById(201))
if (competition.length !== 2 || new Set(competition.map((loop) => loop.agent)).size !== 2) {
  throw new Error('Task #201 competition must use two different claimants')
}
if (loopsFor(taskById(301)).length !== 0) throw new Error('Unclaimed Task #301 cannot have a LOOP')
for (const task of tasks) {
  if (task.parent && !taskById(task.parent)) throw new Error(`Task #${task.id} has an invalid parent`)
  const claimants = loopsFor(task).map((loop) => loop.agent)
  if (new Set(claimants).size !== claimants.length) throw new Error(`Task #${task.id} repeats a claimant LOOP`)
}
if (!root.nativeSubagents?.length || rootLoops.length !== 1) {
  throw new Error('Native subagent must stay inside the parent Thread, not become a Fleet LOOP')
}

const server = await startServer(0)
const base = `http://127.0.0.1:${server.address().port}`
let browser

try {
  browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2 })
  const errors = []
  page.on('pageerror', (error) => errors.push(error.stack || error.message))
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })

  await page.goto(`${base}/#compact`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(350)
  const title = await page.locator('.view-title h2').textContent()
  const taskCount = await page.locator('#view [data-task]').count()
  if (!title || taskCount === 0) throw new Error('A1 view or Task nodes missing')
  if (await page.locator('[data-track], [data-view], #command-phaser, canvas').count()) throw new Error('Game tracks or scenes are still reachable from A1')
  const dimensions = await page.evaluate(() => ({ body: document.body.scrollWidth, viewport: window.innerWidth }))
  if (dimensions.body > dimensions.viewport + 2) throw new Error(`Desktop body horizontal overflow ${dimensions.body}/${dimensions.viewport}`)
  await page.screenshot({ path: `${artifacts}compact.png`, fullPage: true })

  await page.locator('#live-toggle').click()
  if ((await page.locator('#live-label').textContent()) !== 'PAUSED') throw new Error('Live pause control failed')
  await page.locator('#live-toggle').click()
  if ((await page.locator('#live-label').textContent()) !== 'LIVE') throw new Error('Live resume control failed')

  await page.locator('#view [data-task="101"]').first().click()
  await page.locator('#detail-drawer.open').waitFor()
  const scope = page.locator('#detail-drawer')
  const executionText = await scope.locator('.execution-context').textContent()
  if (!executionText.includes('LOOP #12') || !executionText.includes('v2') || !executionText.includes('3 RUNS')) {
    throw new Error('Default LOOP/version/RUN context is wrong')
  }
  if (await scope.locator('.competing-loops').count()) throw new Error('Normal Task shows competition UI')
  if (!(await scope.locator('.conversation-head > b').textContent()).includes('c41f9a2e')) throw new Error('Return created a different Thread')
  if (await scope.locator('[data-loop-id="11"]').count()) throw new Error('Forbidden historical LOOP #11 rendered')
  const messageText = await scope.locator('.message-list').textContent()
  if (!messageText.includes('同一 LOOP #12') || !messageText.includes('退回补充')) throw new Error('Return history is not inside the original Thread')
  const nativeCount = await scope.locator('.native-subagent-fold').count()
  const nativeFolded = nativeCount > 0 && await scope.locator('.native-subagent-fold:not([open])').count() === nativeCount
  if (!nativeFolded || !(await scope.locator('.native-subagent-fold').textContent()).includes('c41f9a2e')) throw new Error('Native subagent left its parent Thread')

  const initialMessages = await scope.locator('.chat-message').count()
  const toolCount = await scope.locator('.message-tool').count()
  const toolFolded = toolCount > 0 && await scope.locator('.message-tool:not([open])').count() === toolCount
  await scope.locator('#task-message-input').fill('请补充 A1 视图中的下一条执行依据')
  await scope.locator('#task-message-form button[type="submit"]').click()
  await page.waitForFunction((before) => document.querySelectorAll('#detail-drawer .chat-message').length >= before + 2, initialMessages)
  const messageCount = await scope.locator('.chat-message').count()
  if (!toolFolded || !(await scope.locator('.drawer-output').first().isVisible())) throw new Error('Conversation context missing')
  await page.screenshot({ path: `${artifacts}conversation-desktop.png` })

  await scope.locator('.drawer-close').click()
  await page.locator('#view [data-task="101"]').first().click()
  if (await scope.locator('.chat-message').count() !== messageCount) throw new Error('Messages did not persist')
  await scope.locator('.drawer-lineage [data-task="102"]').click()
  if (!(await scope.locator('.drawer-header h2').textContent()).includes('收集销售数据')) throw new Error('Child Task navigation failed')
  if (!(await scope.locator('.conversation-head > b').textContent()).includes('a83d20bc')) throw new Error('Child Task reused parent Thread')
  await scope.locator('.drawer-close').click()

  await page.locator('#view [data-task="201"]').first().click()
  await page.locator('#detail-drawer.open').waitFor()
  const competing = scope.locator('.competing-loops')
  if (await competing.count() !== 1 || await competing.getAttribute('open') !== null) throw new Error('Competition must be collapsed')
  await competing.locator('summary').click()
  await scope.locator('[data-loop-id="13"]').click()
  if (!(await scope.locator('.conversation-head > b').textContent()).includes('e91c0a44')) throw new Error('Competing LOOP did not switch Thread')
  if (!(await scope.locator('.drawer-output').textContent()).includes('LOOP #13')) throw new Error('OUTPUT did not switch with LOOP')
  await competing.locator('summary').click()
  await page.screenshot({ path: `${artifacts}competing-loops.png` })
  await scope.locator('.drawer-close').click()

  await page.locator('#view [data-task="301"]').first().click()
  await page.locator('#detail-drawer.open').waitFor()
  if (!(await scope.locator('.empty-instance').count()) || await scope.locator('#task-message-form').count()) throw new Error('Unclaimed Task has an Agent composer')
  await scope.locator('.drawer-close').click()

  for (const id of retiredViews) {
    await page.goto(`${base}/#${id}`, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(100)
    if (new URL(page.url()).hash !== '#compact' || !(await page.locator('#view').getAttribute('class')).includes('view-compact')) throw new Error(`${id}: retired view is still reachable`)
  }

  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`${base}/#compact`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(300)
  await page.locator('#toast').evaluate((element) => { element.hidden = true })
  const mobileDimensions = await page.evaluate(() => ({ body: document.body.scrollWidth, viewport: window.innerWidth }))
  if (mobileDimensions.body > mobileDimensions.viewport + 2) throw new Error(`Mobile body horizontal overflow ${mobileDimensions.body}/${mobileDimensions.viewport}`)
  await page.screenshot({ path: `${artifacts}compact-mobile.png`, fullPage: true })
  await page.locator('#view [data-task="101"]').first().click()
  await page.locator('#detail-drawer.open').waitFor()
  const drawerWidth = await page.locator('#detail-drawer').evaluate((element) => ({ content: element.scrollWidth, box: element.clientWidth }))
  if (drawerWidth.content > drawerWidth.box + 2) throw new Error('Mobile conversation overflow')
  if (!(await scope.locator('#task-message-input').isVisible())) throw new Error('Mobile composer missing')
  await page.screenshot({ path: `${artifacts}conversation-mobile.png` })

  if (errors.length) throw new Error(`Browser errors: ${errors.join(' | ')}`)
  console.log(JSON.stringify({ ok: true, view: 'A1 compact', taskCount, modelChecks: 'passed', conversation: 'passed', childThread: 'passed', competingLoop: 'passed', unclaimedTask: 'passed', liveToggle: 'passed', retiredViews, mobile: 'passed', screenshots: artifacts }, null, 2))
} finally {
  if (browser) await browser.close()
  await new Promise((resolve) => server.close(resolve))
}
