import { expect, test, type Page } from '@playwright/test'

async function mockConsole(page: Page) {
  let messages: unknown[] = []
  const created: unknown[] = []
  let taskCreates = 0
  await page.route('**/api/console/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/api/console/tasks' && request.method() === 'GET') return json([])
    if (path === '/api/console/tasks' && request.method() === 'POST') {
      taskCreates += 1
      return json({ task_id: 99 })
    }
    if (path === '/api/console/agents' && request.method() === 'GET') return json([])
    if (path === '/api/console/threads' && request.method() === 'POST') {
      created.push(request.postDataJSON())
      return json({ thread_id: 'browser-ops-thread' })
    }
    if (path === '/api/console/threads/browser-ops-thread' && request.method() === 'GET') {
      return json({ thread: { thread_id: 'browser-ops-thread', assistant_id: 'fleet-ops-01' }, runs: [], checkpoints: 0 })
    }
    if (path === '/api/console/threads/browser-ops-thread/messages' && request.method() === 'GET') return json(messages)
    if (path === '/api/console/threads/browser-ops-thread/messages' && request.method() === 'POST') {
      const content = request.postDataJSON().content as string
      messages = [
        { content: { type: 'human', content } },
        { content: { type: 'ai', content: `已收到：${content}` } },
      ]
      return json({ status: 'ok' })
    }
    return json({})
  })
  return { created, getTaskCreates: () => taskCreates }
}

test('首页浮窗真实渲染、发送、关闭及刷新恢复', async ({ page }) => {
  const mock = await mockConsole(page)
  await page.goto('/')
  await page.getByRole('button', { name: '打开舰队操作助手' }).click()
  await expect(page.getByRole('region', { name: '舰队操作助手' })).toBeVisible()
  await expect.poll(() => mock.created.length).toBe(1)
  expect(mock.created[0]).toEqual({ assistant_id: 'fleet-ops-01' })

  await page.getByLabel('给舰队操作助手的指令').fill('你好，查询舰队状态')
  await page.getByRole('button', { name: '发送', exact: true }).click()
  await expect(page.getByText('已收到：你好，查询舰队状态')).toBeVisible()
  expect(mock.getTaskCreates()).toBe(0)

  await page.getByRole('button', { name: '关闭操作助手' }).click()
  await page.getByRole('button', { name: '打开舰队操作助手' }).click()
  await expect(page.getByText('已收到：你好，查询舰队状态')).toBeVisible()
  expect(mock.created).toHaveLength(1)

  await page.reload()
  await page.getByRole('button', { name: '打开舰队操作助手' }).click()
  await expect(page.getByText('已收到：你好，查询舰队状态')).toBeVisible()
  expect(mock.created).toHaveLength(1)
})

test('移动视口下浮窗不溢出且仍可操作', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockConsole(page)
  await page.goto('/')
  await page.getByRole('button', { name: '打开舰队操作助手' }).click()
  const panel = page.getByRole('region', { name: '舰队操作助手' })
  await expect(panel).toBeVisible()
  const box = await panel.boundingBox()
  expect(box).not.toBeNull()
  expect(box!.x).toBeGreaterThanOrEqual(0)
  expect(box!.x + box!.width).toBeLessThanOrEqual(390)
  expect(box!.y + box!.height).toBeLessThanOrEqual(844)
  await page.getByLabel('给舰队操作助手的指令').fill('移动端测试')
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeEnabled()
})
