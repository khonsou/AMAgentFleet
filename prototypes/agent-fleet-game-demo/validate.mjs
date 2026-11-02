import { mkdir } from 'node:fs/promises'
import { chromium } from '@playwright/test'
import { createServer } from 'vite'

const artifacts = new URL('./artifacts/', import.meta.url).pathname
await mkdir(artifacts, { recursive: true })
const server = await createServer({ server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' })
await server.listen()
const port = server.httpServer.address().port
const browser = await chromium.launch({ headless: true })
const results = []
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1.5 })
  const errors = []
  page.on('pageerror', (error) => errors.push(error.stack || error.message))
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
  await page.goto(`http://127.0.0.1:${port}`, { waitUntil: 'domcontentloaded' })
  await page.waitForFunction(() => window.__fleetDemo?.stats().agents === 6, { timeout: 10000 }).catch((error) => {
    throw new Error(`Scene initialization failed: ${errors.join('\n') || error.message}`)
  })
  await page.waitForTimeout(1200)
  const stats = await page.evaluate(() => window.__fleetDemo.stats())
  if (stats.projects !== 0 || stats.triangles < 1000) throw new Error(`3D scene incomplete: ${JSON.stringify(stats)}\n${errors.join('\n')}`)
  if (await page.locator('.agent-row').count() !== 6) throw new Error('Agent registry is incomplete')
  if (await page.locator('.task-bubble').count() !== 5) throw new Error('Progress bubbles do not match active Agents')
  if (await page.locator('.project-label').count() !== 0) throw new Error('Project tables are still visible')
  await page.screenshot({ path: `${artifacts}desktop-overview.png` })
  results.push({ check: '3D overview without Project tables', ...stats })

  await page.locator('#play-toggle').click()
  await page.locator('.agent-name').filter({ hasText: 'COMMANDER' }).click({ force: true })
  await page.locator('#detail-drawer.open').waitFor()
  const agentDrawer = await page.locator('#detail-drawer').textContent()
  if (!agentDrawer.includes('T-101') || !agentDrawer.includes('T-104')) throw new Error('Agent name does not list all Tasks')
  await page.screenshot({ path: `${artifacts}agent-tasks.png` })
  await page.locator('[data-open-task="T-101"]').click()
  if (!(await page.locator('#detail-drawer').textContent()).includes('生成 Q3 业务复盘')) throw new Error('Task entry opened wrong group chat')
  await page.screenshot({ path: `${artifacts}task-chat.png` })
  await page.locator('.drawer-close').click()
  results.push({ check: 'Agent name lists Tasks and opens group chat', passed: true })

  const agentPoint = await page.evaluate(() => window.__fleetDemo.screenPoint('agent', 'writer'))
  await page.mouse.click(agentPoint.x, agentPoint.y)
  await page.waitForTimeout(350)
  if (!(await page.locator('#detail-drawer').textContent()).includes('WRITER')) throw new Error('3D Agent raycast selection failed')
  await page.locator('.drawer-close').click()
  results.push({ check: '3D Agent selection', passed: true })

  const bubbleOpacity = () => page.locator('.task-label').first().evaluate((element) => Number(getComputedStyle(element).opacity))
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('.task-label')).opacity) < 0.1, { timeout: 12000 })
  const faded = await bubbleOpacity()
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('.task-label')).opacity) > 0.9, { timeout: 12000 })
  const reappeared = await bubbleOpacity()
  if (faded > 0.1 || reappeared < 0.9) throw new Error('Progress bubble did not fade out and reappear')
  results.push({ check: 'Progress bubbles fade out and reappear', faded, reappeared })

  for (let i = 0; i < 4; i += 1) await page.evaluate(() => window.__fleetDemo.nextEvent())
  const accepted = await page.evaluate(() => ({ projects: window.__fleetDemo.state.projects.length, status: window.__fleetDemo.state.tasks.find((task) => task.id === 'T-202').status, index: window.__fleetDemo.state.eventIndex }))
  if (accepted.projects !== 0 || accepted.status !== 'done' || accepted.index !== 4) throw new Error(`Task OUTPUT acceptance failed: ${JSON.stringify(accepted)}`)
  results.push({ check: 'OUTPUT is accepted inside Task without Project semantics', passed: true })

  for (let i = 0; i < 4; i += 1) await page.evaluate(() => window.__fleetDemo.nextEvent())
  const claim = await page.evaluate(() => window.__fleetDemo.state.tasks.find((task) => task.id === 'T-301'))
  if (claim.agent !== 'researcher' || claim.status !== 'delivered' || claim.project) throw new Error('Independent Task lifecycle failed')
  results.push({ check: 'Independent Task claim and delivery', passed: true })

  await page.evaluate(() => window.__fleetDemo.reset())
  await page.locator('#play-toggle').click()
  await page.locator('[data-view="pool"]').click()
  await page.waitForTimeout(1500)
  const poolZoom = await page.evaluate(() => window.__fleetDemo.stats().zoom)
  if (poolZoom < 1.3) throw new Error('Task Pool camera did not zoom')
  await page.screenshot({ path: `${artifacts}task-pool.png` })
  await page.locator('.toggle-row').first().click()
  await page.waitForTimeout(100)
  const visibleTasks = await page.locator('.task-label:visible').count()
  if (visibleTasks !== 0) throw new Error('Task layer toggle failed')
  await page.locator('.toggle-row').first().click()
  await page.locator('#tour-toggle').click()
  if ((await page.evaluate(() => window.__fleetDemo.stats())).autoRotate) throw new Error('Auto tour toggle failed')
  results.push({ check: 'Camera, layers and auto tour controls', passed: true })

  await page.setViewportSize({ width: 390, height: 844 })
  await page.evaluate(() => window.__fleetDemo.reset())
  await page.waitForTimeout(3600)
  const dimensions = await page.evaluate(() => ({ body: document.body.scrollWidth, viewport: innerWidth }))
  if (dimensions.body > dimensions.viewport + 2) throw new Error('Mobile horizontal overflow')
  await page.screenshot({ path: `${artifacts}mobile-overview.png` })
  results.push({ check: 'Mobile layout', overflow: false })

  if (errors.length) throw new Error(`Browser errors:\n${errors.join('\n')}`)
  console.log(JSON.stringify({ ok: true, results }, null, 2))
} finally {
  await browser.close()
  await server.close()
}
