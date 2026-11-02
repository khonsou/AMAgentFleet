export const agents = [
  { id: 'commander', name: 'commander', role: 'Coordinator', status: 'working', model: 'deepseek-v4', tokens: 6840 },
  { id: 'analyst', name: 'analyst', role: 'Data analysis', status: 'working', model: 'deepseek-v4', tokens: 7920 },
  { id: 'researcher', name: 'researcher', role: 'Research', status: 'idle', model: 'deepseek-v4', tokens: 5240 },
  { id: 'writer', name: 'writer', role: 'Long-form writing', status: 'waiting', model: 'deepseek-v4', tokens: 6180 },
  { id: 'reviewer', name: 'reviewer', role: 'Quality review', status: 'error', model: 'deepseek-v4', tokens: 4360 },
  { id: 'scout', name: 'scout', role: 'Market watch', status: 'waiting', model: 'deepseek-v4', tokens: 8060 },
]

// 与现行 Fleet 模型对齐：parent 表示 parent_task_id；loop/thread 是默认执行实例。
// 只有 competingLoops 表示不同 claimant 的竞争领取，不表示退回、重试或 RUN 历史。
export const tasks = [
  {
    id: 101, parent: null, title: '生成 2026 Q3 业务复盘', agent: 'commander', status: 'running', pool: 'open',
    loop: 12, thread: 'c41f9a2e', version: 2, runs: 3, modelPolicy: 'inherit', tokens: 6840,
    output: '已汇总 3 个业务线结果，等待销售数据与风险复核完成。',
    start: 4, duration: 91, stage: 'aggregate', updated: '刚刚',
    nativeSubagents: [
      { id: '6fa2c1', type: 'general-purpose', description: '核对统计口径', status: 'running' },
    ],
  },
  {
    id: 102, parent: 101, title: '收集销售数据', agent: 'analyst', status: 'running', pool: 'open',
    loop: 8, thread: 'a83d20bc', version: 1, runs: 1, tokens: 7920,
    output: '华东与华南数据已对齐，正在补齐渠道维度。',
    start: 12, duration: 70, stage: 'execute', updated: '12 秒前',
  },
  {
    id: 103, parent: 101, title: '客户反馈聚类', agent: 'researcher', status: 'success', pool: 'done',
    loop: 5, acceptedLoop: 5, thread: 'f77b19e0', version: 1, runs: 1, tokens: 5240,
    output: '1,284 条反馈归为 6 类；交付速度与 API 稳定性占负面反馈的 61%。',
    start: 8, duration: 35, stage: 'done', updated: '4 分钟前',
  },
  {
    id: 104, parent: 101, title: '生成分析初稿', agent: 'writer', status: 'delivered', pool: 'review',
    loop: 9, thread: 'b92e77ad', version: 1, runs: 1, tokens: 6180,
    output: 'Q3 收入环比增长 18%，但企业客户续约率下降 4.2%。建议优先核查 onboarding 周期与响应 SLA。',
    start: 31, duration: 42, stage: 'review', updated: '1 分钟前',
  },
  {
    id: 105, parent: 101, title: '复核风险与异常', agent: 'reviewer', status: 'error', pool: 'open',
    loop: 4, thread: 'e20c61fa', version: 1, runs: 2, tokens: 4360,
    output: '已完成 2/5 项检查。',
    error: '上游数据源返回字段缺失：renewal_rate。可重试或回到 Thread 修正。',
    start: 44, duration: 24, stage: 'error', updated: '38 秒前',
  },
  {
    id: 201, parent: null, title: '整理本周竞品动态', agent: 'scout', status: 'delivered', pool: 'review',
    loop: 7, thread: 'd813a90c', version: 1, runs: 1, tokens: 10460, loopTokens: 8060,
    output: '3 家竞品调整企业版定价，2 家发布 Agent 协作功能。已附来源与对比表。',
    start: 17, duration: 58, stage: 'review', updated: '2 分钟前',
    competingLoops: [
      {
        id: 13, agent: 'analyst', model: 'deepseek-v4', thread: 'e91c0a44', status: 'delivered',
        version: 1, runs: 1, tokens: 2400, output: '另一认领者补充了 5 家竞品的价格锚点，结论与默认 LOOP 可并排验收。',
        start: 48, duration: 32, updated: '3 分钟前',
      },
    ],
  },
  {
    id: 202, parent: 201, title: '对比定价变化', agent: 'analyst', status: 'success', pool: 'done',
    loop: 6, acceptedLoop: 6, thread: 'aa19f824', version: 1, runs: 1, tokens: 3120,
    output: '企业版平均涨幅 11.7%，usage-based 套餐占比提高。',
    start: 21, duration: 29, stage: 'done', updated: '6 分钟前',
  },
  {
    id: 301, parent: null, title: '巡检 Fleet 运行状态', agent: 'unassigned', status: 'queued', pool: 'open',
    loop: null, thread: null, tokens: 0, output: '',
    start: 76, duration: 12, stage: 'queued', updated: '刚刚',
  },
]

export const events = [
  { time: 'NOW', agent: 'analyst', text: '正在补齐渠道维度', kind: 'running' },
  { time: '-38s', agent: 'reviewer', text: '字段缺失，等待处理', kind: 'error' },
  { time: '-1m', agent: 'writer', text: 'LOOP #9 已提交 OUTPUT', kind: 'review' },
  { time: '-2m', agent: 'scout', text: '两个竞争 LOOP 等待验收', kind: 'review' },
  { time: '-4m', agent: 'researcher', text: '客户反馈聚类完成', kind: 'success' },
]

export const status = {
  running: { label: '执行中', tone: 'green', symbol: '●' },
  success: { label: '已结束', tone: 'muted', symbol: '✓' },
  delivered: { label: '待验收', tone: 'amber', symbol: '◆' },
  error: { label: '异常', tone: 'red', symbol: '!' },
  queued: { label: '待领取', tone: 'cyan', symbol: '○' },
}

export const taskById = (id) => tasks.find((task) => task.id === Number(id))
export const agentById = (id) => agents.find((agent) => agent.id === id)
export const childrenOf = (id) => tasks.filter((task) => task.parent === Number(id))
export const roots = () => tasks.filter((task) => task.parent === null)
export const formatTokens = (value) => value >= 1000 ? `${(value / 1000).toFixed(1)}k` : String(value)

export function descendantsOf(id) {
  return childrenOf(id).flatMap((child) => [child, ...descendantsOf(child.id)])
}

export function tagOf(task) {
  if (task.pool === 'done') return 'done'
  if (task.pool === 'review') return 'review'
  if (task.status === 'error') return 'error'
  if (task.agent === 'unassigned') return 'queued'
  return 'running'
}
