#!/usr/bin/env node
/**
 * AM Agent Fleet —— FAP v1 第三方 agent 接入参考实现（ADR-0011，协议 1.0）
 *
 * 零依赖、Node ≥ 22 直跑。演示第三方 agent 仅凭 `/api/meta` + PAT 的完整闭环：
 *
 *   自发现：GET /api/meta（免鉴权）探明能力 → 缺 tasks/pat_auth 能力明确退出；
 *   各响应 X-Protocol-Version 与本脚本预期大版本不一致时告警一次。
 *
 *   主流程（--demo 可加演 409/幂等回放）：
 *     node examples/fap-quickstart.mjs --api <控制面地址> --pat <PAT>
 *       → meta 自发现 → POST /api/tasks 发起（Idempotency-Key + source/actor 透传）
 *       → POST /api/tasks/{id}/loops 领取（human 占位演示人机同构；agent 领取需已注册）
 *       → POST /api/loops/{id}/deliver 交付 → GET /api/tasks/{id} 轮询验收结果
 *
 *   PAT 获取：fleet Console「SETTINGS」页签发（scope 选 write），token 只显示一次。
 *
 * 参数（CLI 优先，环境变量兜底）：--api / FAP_API（缺省 http://localhost:5174，
 *   即 Console 同源入口——GUI 网址就是 API 入口）、--pat / FAP_PAT。
 *   经同源入口的客户端流量一律要求 PAT，无豁免。
 *
 * 注意：这是参考实现而非生产代码——真实 agent 应替换「执行任务」部分；
 * 429 按 retry_after 退避一次；幂等键重试返回首次响应（X-Idempotent-Replay: true）。
 */

// ---------------------------------------------------------------------------
// 参数与环境
// ---------------------------------------------------------------------------
const args = process.argv.slice(2)
const flag = (name) => {
  const i = args.indexOf(name)
  return i >= 0 ? args[i + 1] : undefined
}
const has = (name) => args.includes(name)

const API = (flag('--api') ?? process.env.FAP_API ?? 'http://localhost:5174').replace(/\/+$/, '')
const PAT = flag('--pat') ?? process.env.FAP_PAT
const DEMO = has('--demo')

function die(msg) {
  console.error(`✗ ${msg}`)
  process.exit(1)
}
if (!PAT) die('缺少 PAT：--pat <PAT> 或环境变量 FAP_PAT（fleet Console SETTINGS 页签发，scope=write）')

// ---------------------------------------------------------------------------
// HTTP 辅助：429 按 retry_after 退避一次；协议大版本不一致告警一次
// ---------------------------------------------------------------------------
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const EXPECTED_PROTOCOL_MAJOR = '1'
let versionWarned = false
const RUN_ID = `fap-quickstart-${new Date().toISOString()}`

async function api(method, p, { body, idemKey } = {}) {
  for (let attempt = 0; ; attempt++) {
    let res
    try {
      res = await fetch(`${API}/api${p}`, {
        method,
        headers: {
          'content-type': 'application/json',
          authorization: `Bearer ${PAT}`,
          ...(idemKey ? { 'idempotency-key': idemKey } : {}),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
    } catch (e) {
      die(`无法连接 ${API}（${e.cause?.code ?? e.message}）——请确认 control-plane 已启动、--api 地址正确`)
    }
    const pv = res.headers.get('x-protocol-version')
    if (!versionWarned && pv && pv.split('.')[0] !== EXPECTED_PROTOCOL_MAJOR) {
      versionWarned = true
      console.warn(`⚠ 实例协议版本 ${pv} 与本脚本预期 ${EXPECTED_PROTOCOL_MAJOR}.x 不一致，行为可能出入（详见 ${API}/api/agent-doc）`)
    }
    const json = await res.json().catch(() => null)
    if (res.status === 429 && attempt === 0) {
      const wait = Number(json?.retry_after ?? 1)
      console.error(`  … 限速 429，按 retry_after 等待 ${wait} 秒后重试`)
      await sleep(wait * 1000)
      continue
    }
    return { status: res.status, body: json, headers: res.headers }
  }
}

// ---------------------------------------------------------------------------
// 自发现（H1）：正式通信前 GET /api/meta（免鉴权）
// ---------------------------------------------------------------------------
async function selfDiscovery() {
  const r = await api('GET', '/meta')
  if (r.status !== 200 || !Array.isArray(r.body?.capabilities)) {
    die(`自发现失败：GET /api/meta 返回 HTTP ${r.status}——对面可能不是 fleet 控制面或版本过老`)
  }
  const caps = r.body.capabilities
  for (const need of ['tasks', 'pat_auth']) {
    if (!caps.includes(need)) die(`实例未声明 ${need} 能力（capabilities: ${caps.join(', ')}），本脚本无法运行`)
  }
  console.log(`✓ 自发现：协议 ${r.body.protocol_version} / server ${r.body.server_version} / 能力: ${caps.join('、')}`)
  console.log(`  完整文档: ${API}${r.body.doc}`)
}

// ---------------------------------------------------------------------------
// 主流程：发起 → 领取 → 交付 → 轮询验收（ADR-0010 状态机）
// ---------------------------------------------------------------------------
async function main() {
  await selfDiscovery()

  // 1. 发起（开放领取；Idempotency-Key 保证网络重试不重复建任务）
  const t = await api('POST', '/tasks', {
    idemKey: `${RUN_ID}-create`,
    body: {
      input: 'FAP quickstart 示例任务：请汇报你能访问到的 fleet 任务池概况（任务总数/待确认数）',
      source: { type: 'agent', external_run_id: RUN_ID }, // 自报来源，透传进审计
      actor: { type: 'agent', id: 'fap-quickstart' },     // 自报执行者
    },
  })
  if (t.status === 401) die('PAT 无效或已吊销（401）')
  if (t.status === 403) die('PAT scope 不足（403）——本脚本需要 write scope')
  if (t.status !== 201) die(`发起任务失败（HTTP ${t.status}）: ${JSON.stringify(t.body)}`)
  const taskId = t.body.task_id
  console.log(`✓ 任务已发起: task ${taskId}（pool_status: ${t.body.pool_status}）`)

  // 2. 领取（演示 human 占位领取：人机同构，agent 领取把 claimant_type 换 'agent' + 已注册 agent 名）
  const c = await api('POST', `/tasks/${taskId}/loops`, {
    body: { claimant_type: 'human', claimant: 'human' },
  })
  if (c.status === 409) {
    console.log(`  … 任务已被领满 3 loop（409）——换一个任务或稍后重试；本示例继续用既有 loop 交付`)
  } else if (c.status !== 201) {
    die(`领取失败（HTTP ${c.status}）: ${JSON.stringify(c.body)}`)
  }
  const loopId = c.body?.loop_id
  console.log(`✓ 已领取: loop ${loopId}`)

  // 3. 执行……（真实 agent 在此干活；quickstart 直接交付示例结果）
  const d = await api('POST', `/loops/${loopId}/deliver`, {
    idemKey: `${RUN_ID}-deliver`,
    body: {
      content: 'quickstart 示例交付：这是一个模拟结果。真实 agent 在此提交自己的产出（文本摘要 + 产物链接）。',
      actor: { type: 'agent', id: 'fap-quickstart' },
    },
  })
  if (d.status !== 200) die(`交付失败（HTTP ${d.status}）: ${JSON.stringify(d.body)}`)
  console.log(`✓ 已交付: loop ${loopId} → 任务进入验收台（review）`)

  // 4. 轮询验收结果（第三方 agent 不自行采纳——采纳是人的强制动作，ADR-0010）
  const g = await api('GET', `/tasks/${taskId}`)
  console.log(`✓ 任务状态: pool_status=${g.body?.task?.pool_status}（review=待人工采纳；done=已验收）`)
  console.log('\n下一步：验收人到 fleet Console 验收台采纳或打回；打回则同 thread 续跑修正后重新交付。')
}

// ---------------------------------------------------------------------------
// --demo：演示 409（幂等键跨 actor 冲突由服务端保证，此处演示同键重试的回放语义）
// ---------------------------------------------------------------------------
async function demoReplay() {
  const key = `${RUN_ID}-replay-demo`
  const r1 = await api('POST', '/tasks', { idemKey: key, body: { input: '幂等回放演示任务' } })
  const r2 = await api('POST', '/tasks', { idemKey: key, body: { input: '幂等回放演示任务（重试，服务端应忽略）' } })
  const replay = r2.headers.get('x-idempotent-replay')
  console.log(`首次: task ${r1.body?.task_id}（HTTP ${r1.status}）`)
  console.log(`重试: task ${r2.body?.task_id}（HTTP ${r2.status}，X-Idempotent-Replay: ${replay ?? 'false'}）`)
  if (r1.body?.task_id === r2.body?.task_id) console.log('✓ 同键重试返回首次响应，未重复创建')
  else die('幂等回放异常：两次返回不同 task_id')
}

if (DEMO) {
  await selfDiscovery()
  await demoReplay()
} else {
  await main()
}
