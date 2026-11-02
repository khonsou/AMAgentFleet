export const agents = [
  { id: 'commander', name: 'COMMANDER', role: 'Orchestration', color: '#ef5b2a', status: 'working', project: 'atlas', position: [-2.2, 0, 1.6], tasks: ['T-101', 'T-104'] },
  { id: 'analyst', name: 'ANALYST', role: 'Data analysis', color: '#e7ad4a', status: 'working', project: 'atlas', position: [-4.3, 0, 0.3], tasks: ['T-102', 'T-105', 'T-106'] },
  { id: 'researcher', name: 'RESEARCHER', role: 'Research', color: '#7a9c88', status: 'idle', project: null, position: [1.7, 0, 2.7], tasks: [] },
  { id: 'writer', name: 'WRITER', role: 'Long-form writing', color: '#d9846f', status: 'waiting', project: 'atlas', position: [-3.7, 0, 3.1], tasks: ['T-103'] },
  { id: 'reviewer', name: 'REVIEWER', role: 'Quality review', color: '#ab6b62', status: 'error', project: 'relay', position: [4.3, 0, -0.2], tasks: ['T-201'] },
  { id: 'scout', name: 'SCOUT', role: 'Market watch', color: '#7398aa', status: 'working', project: 'relay', position: [3.0, 0, -2.0], tasks: ['T-202'] },
]

export const projects = [
  { id: 'atlas', name: 'PROJECT ATLAS', subtitle: 'Q3 Business Review', position: [-3.2, 0, 1.5], color: '#ef5b2a', documents: 12, outputs: 7, status: 'active', description: '汇总业务线表现，定位续约风险并形成下一季度行动建议。' },
  { id: 'relay', name: 'PROJECT RELAY', subtitle: 'Competitive Intelligence', position: [3.5, 0, -0.8], color: '#7a9c88', documents: 8, outputs: 4, status: 'active', description: '持续追踪竞品价格、产品发布与企业级 Agent 协作能力。' },
]

export const tasks = [
  { id: 'T-101', title: '生成 Q3 业务复盘', short: '正在编排', agent: 'commander', project: 'atlas', status: 'working', progress: 68, messages: 18, output: null, detail: '已拆分销售数据、客户反馈与风险复核，正在汇总各工作流的结果。' },
  { id: 'T-102', title: '核对销售数据口径', short: '分析数据', agent: 'analyst', project: 'atlas', status: 'working', progress: 54, messages: 11, output: null, detail: '华东与华南数据已对齐，正在补齐渠道维度。' },
  { id: 'T-103', title: '生成分析初稿', short: '等待指导', agent: 'writer', project: 'atlas', status: 'waiting', progress: 82, messages: 9, output: 'Q3 收入环比增长 18%，企业客户续约率下降 4.2%。', detail: '初稿已交付，等待人确认风险表述与行动建议。' },
  { id: 'T-104', title: '整理复盘会议结论', short: '汇总结论', agent: 'commander', project: 'atlas', status: 'working', progress: 31, messages: 5, output: null, detail: '正在整理各 Task 群聊中已经确认的结论。' },
  { id: 'T-105', title: '复核续约数据', short: '核对异常', agent: 'analyst', project: 'atlas', status: 'working', progress: 43, messages: 7, output: null, detail: '对比客户续约率与 onboarding 周期。' },
  { id: 'T-106', title: '整理行动项 owner', short: '待处理', agent: 'analyst', project: 'atlas', status: 'waiting', progress: 18, messages: 3, output: null, detail: '等待复盘结论确认后分配行动项。' },
  { id: 'T-201', title: '复核竞品数据异常', short: '数据源异常', agent: 'reviewer', project: 'relay', status: 'error', progress: 37, messages: 8, output: null, detail: '上游数据源缺少 renewal_rate 字段，需要人工确认替代口径。' },
  { id: 'T-202', title: '整理本周竞品动态', short: 'OUTPUT 已交付', agent: 'scout', project: 'relay', status: 'delivered', progress: 100, messages: 14, output: '3 家竞品调整企业版定价，2 家发布 Agent 协作功能。', detail: '交付已提交到 Task 群聊，等待采纳。' },
  { id: 'T-301', title: '巡检 Fleet 运行状态', short: '待领取', agent: null, project: null, status: 'queued', progress: 0, messages: 0, output: null, detail: '独立 Task，等待从 Task Pool 领取。' },
]

export const demoEvents = [
  { id: 'brief', label: '指挥官完成拆解', task: 'T-101', agent: 'commander', progress: 76, short: '拆解完成', toast: 'COMMANDER 已完成任务拆解，群聊新增 3 条消息。' },
  { id: 'analysis', label: '销售数据完成对齐', task: 'T-102', agent: 'analyst', progress: 92, short: '即将交付', toast: 'ANALYST 已对齐销售数据，正在生成 OUTPUT。' },
  { id: 'delivery', label: '竞品动态提交 OUTPUT', task: 'T-202', agent: 'scout', status: 'delivered', progress: 100, short: '等待验收', toast: 'SCOUT 在 Task 群聊中提交了 OUTPUT。' },
  { id: 'accepted', label: '竞品动态 OUTPUT 已采纳', task: 'T-202', agent: 'scout', status: 'done', progress: 100, short: '已采纳', toast: '你已在 Task 群聊中采纳 SCOUT 的 OUTPUT。' },
  { id: 'guide', label: '人加入初稿群聊', task: 'T-103', agent: 'writer', status: 'working', progress: 88, short: '根据指导修改', toast: '你已在群聊中给出指导，WRITER 继续修改。' },
  { id: 'recover', label: '数据异常已恢复', task: 'T-201', agent: 'reviewer', status: 'working', progress: 64, short: '恢复复核', toast: '字段口径已确认，REVIEWER 从原 Task 继续。' },
  { id: 'claim', label: 'Researcher 领取巡检', task: 'T-301', agent: 'researcher', status: 'working', progress: 24, short: '正在巡检', toast: 'RESEARCHER 从 Task Pool 领取了 Task。' },
  { id: 'finish', label: '巡检 OUTPUT 已交付', task: 'T-301', agent: 'researcher', status: 'delivered', progress: 100, short: 'OUTPUT 已交付', output: 'Fleet 服务运行正常，1 项配置建议复核。', toast: '巡检 Task 已在群聊中完成交付。' },
]

export const initialSnapshot = () => ({
  tasks: structuredClone(tasks).map(({ project, ...task }) => task),
  projects: [],
  agents: structuredClone(agents).map(({ project, ...agent }) => agent),
  eventIndex: 0,
})
