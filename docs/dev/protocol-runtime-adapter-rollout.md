# 协议化收尾与 RuntimeAdapter 分阶段实施拆解

- **状态**：实施中
- **日期**：2026-11-01
- **分支**：`feat/protocol-runtime-adapter`
- **决策依据**：[ADR-0031](../adr/0031-unified-protocol-playground-and-task-conversation.md)（已接受）、[ADR-0032](../adr/0032-runtime-adapter-and-cold-switch.md)（已接受）

## 0. 目标与开工门槛

把两项高风险工作拆成可独立评审、独立验收、独立回滚的小 PR：

- **工作包 A**：旧 Console GUI 全部改用共享 Protocol Client，完成 ADR-0031 的既有决策。
- **工作包 B**：先用 `DeerFlowAdapter` 隔离现有运行时，再按 ADR-0002 Option B 接入 DeepSeek OpenAI 兼容薄 Runner 参考实现。

ADR-0032 已接受，A／B 各阶段可在本分支连续实施；每个阶段仍须独立验收和提交。第二套 Harness 只在测试环境显式启用，不授权生产切换。

## 1. 不可突破的范围

1. 不改变 FAP operation ID、Task／Loop 状态机、强制验收和审计语义。
2. 不让浏览器调用 `/internal/ops/execute`，不让任何 Runtime 接触系统 PAT 或业务原始 token。
3. 不在同一 PR 中同时移动大量文件和改变业务行为；每个 PR 只承担一个回滚原因。
4. 不在第一轮支持 per-Agent 混跑、热切换、跨 Runtime Checkpoint 迁移或统一可移植 AgentSpec。
5. 不删除旧 Console 服务端兼容端点；第一阶段只停止前端对它们的业务调用。
6. 不把“第三方 Agent 经 FAP 接入”误当作“内部 Harness 已可替换”。

## 2. 工作包 A：旧 GUI 协议化收尾

### A1．抽共享 Protocol Client，无行为变化

**状态**：✅ 已完成（2026-11-01）

**改动**

- 将 `console/frontend/src/playground/protocol/` 的 client、provider、context、hook 移到 `console/frontend/src/protocol/`。
- Playground 改为引用公共实现；确认框样式从 Playground 专属样式中解耦。
- 在 `App.tsx` 的认证区域挂载统一 `OperationProvider`，暂不迁移旧页面调用。

**验收**

- 现有 Playground 单元测试、浏览器测试和写确认流程不变。
- 旧 Console 页面行为与网络请求不变；本 PR 没有业务迁移。

**回滚**：直接 revert；不涉及协议、数据库或服务端。

### A2．迁移只读调用，保留页面结构

**状态**：✅ 已完成（2026-11-01）

**改动**

- 在 `lib/api.ts` 保留现有 `api.xxx()` 签名，只读方法内部统一调用 operation ID，页面无需改动。
- 分批迁移 Home、Registry、Threads、ThreadDetail、AgentDetail、TaskDetail、Settings 的读操作。
- Agent 专属任务查询统一为 `tasks.list(query.agent=...)`；类型定义与 HTTP 实现分离。
- 保留 `console/backend/operations.py` 的服务端 operation 解析、真实 actor 覆盖与受信转发。

**验收**

- 每页数据与迁移前一致；空态、分页、401、404、409 和轮询行为不变。
- 页面不再直接发起业务 GET；Playground 与旧 GUI 共用同一协议缓存和错误语义。

**回滚**：按页面或整个 PR revert；旧后端只读端点仍在。

### A3．迁移写操作与 human-only 表单

**状态**：✅ 已完成（2026-11-01）

**改动**

- 通过 App 级 Provider 注册的 `guiExecutor` 写桥迁移 Agent、Task、Loop、Thread、Inbox、MCP、模板、模型和 PAT 写操作，保留既有页面调用签名。
- 所有写调用经过统一确认与 `Idempotency-Key`；actor 仍由服务端覆盖，不能信任浏览器自报。
- 校正清单中 human-only 状态变更操作的 `write` 语义，至少覆盖 `models.managed_save`、`pats.issue`、`ops.credential_rotate`；human-only 继续禁止 Agent 执行。
- `Registry.tsx` 的模板渲染直连改为 `templates.render`。

**验收**

- 未确认不写；重复提交不重复建 Task、Loop、PAT 或配置版本。
- DAO 身份、source／actor 审计、409 冲突和 429 退避与现有语义一致。
- PAT、非空 secrets 和系统凭证只走人类表单，秘密不出现在 Thread、日志和审计正文。

**回滚**：立即 revert 对应写操作迁移；保留后端兼容端点，禁止为回滚绕过确认或鉴权。

### A4．特殊传输、清理与防回退

**状态**：✅ 已完成（2026-11-01）

**改动**

- 产物下载统一复用 Protocol Client 二进制 adapter；旧 GUI 当前未使用 `runs.stream`，本阶段不新增 SSE 调用。
- OAuth 仍属身份基础设施，不纳入 Fleet 业务 operation。
- `lib/api.ts` 保留类型与协议兼容 façade；新增静态测试，禁止页面新增业务直连 `fetch`，并校验全部 operation ID 与读写属性。
- 补齐 Console／Control Plane 契约测试和旧 GUI 浏览器回归。

**完成门 G1**：✅ 已达到

- 旧 Console 前端业务读写均使用 operation ID；仅 OAuth、二进制和 SSE 使用明确的专用传输。
- 现有离线协议验收、前端单测和全栈任务闭环全部通过。
- 未修改 Control Plane 业务路由和数据库。

## 3. 工作包 B：RuntimeAdapter

### B1．先冻结 DeerFlow 行为契约

**状态**：✅ 已完成（2026-11-01）

**前置**：ADR-0032 已接受；不在本任务选择或接入第二套 Harness。

**改动**

- 新增可复用 Runtime Contract Tests，记录当前 Agent 部署、Run 请求／结果、消息、错误、恢复引用、Token、产物和能力行为。
- 将 `threads.send`、Loop 退回续跑、递归上限恢复、取消和模型 Profile 解析纳入对照夹具；Console 观测 DTO 对照在 B3 落地。

**验收**：契约测试在当前代码上全绿；任何语义不明处先补文档，不在重构时顺手改行为。

### B2．引入 Adapter 与 DeerFlowAdapter，默认零切换

**状态**：✅ 已完成（2026-11-01）

**改动**

- 新增 `control-plane/app/runtime/`：`base.py`、`resolver.py`、`deerflow.py`。
- 定义窄接口与 DTO：Agent 部署／删除、Run 执行／恢复、Thread 状态／消息、取消／删除、模型目录、capabilities。
- Thread／Run／Checkpoint 引用对核心层不透明；错误至少区分临时故障、步数上限、永久失败和能力不支持。
- 把 DeerFlow 原生路径、LangGraph payload、错误字符串解析和 `deerflow.custom-agent` 校验移入 Adapter。
- `FLEET_RUNTIME` 缺省固定为 `deerflow`；未知值启动失败，不静默回退。

**验收**

- Registry、Dispatcher、recovery、models 通过 Adapter 运行现有测试。
- 核心模块不再直接构造 DeerFlow 请求；线上仍只有 DeerFlow，无新部署形态。
- 相同夹具下请求语义、交付、审计和错误结果与 B1 基线一致。

**回滚**：恢复直接调用实现或 revert；不改变数据库和 Runtime 状态。

### B3．隔离运行时观测面

**状态**：✅ 已完成（2026-11-01）

**改动**

- Control Plane 提供稳定的 Thread／Run 查询与执行面，转发到 RuntimeAdapter。
- Console 停止直接读取 `threads_meta`、`runs`、`checkpoints`，改为消费标准 DTO。
- `threads.*`、`runs.*` 逐步迁移到稳定 Runtime／Control Plane surface；原生 Gateway surface 暂时保留兼容。
- 对不支持的能力返回 `capability_not_supported`，不得以空数据冒充成功。

**验收**

- 新旧只读结果对照一致，包含消息顺序、Run 状态、Token、Checkpoint 计数和空结果。
- FAP operation ID 与 Task／Loop 语义不变；第三方客户端兼容路径有明确弃用说明。
- Console 的核心观察路径不再依赖 DeerFlow 表结构。

**回滚**：Console 切回只读兼容路径；不得删除 DeerFlow 表或历史数据。

### B4．第二套 Harness 只做垂直切片

**状态**：✅ 已完成（2026-11-01）

**前置**：采用 ADR-0002 Option B 的 DeepSeek OpenAI 兼容薄 Runner；不支持 Checkpoint、SSE、取消、反馈、产物、Managed Models、旧 cron 或 subagent 工具编排，恢复仅走 requeue。

**最小闭环**

> 注册 Agent → 创建 Task → 执行 Loop → 自动交付 → 打回 → resume 或明确降级为 requeue → 验收

**改动边界**

- 新 Adapter 只实现已声明能力，并复用 B1 契约测试。
- `fleet_tools.py` 的 LangChain `RunnableConfig` 集成留在 DeerFlow 侧；新 Harness 使用自己的受控工具桥连接同一 Fleet 内部服务。
- Model Profile 与运行时模型配置通过明确映射转换；API Key 仍由受管秘密通道保存。
- 默认部署保持 DeerFlow，新 Adapter 只能在测试环境显式启用。

**完成门 G2**：✅ 已达到。两种 Runtime 按各自 capabilities 通过契约与 Dispatcher 闭环测试；薄 Runner 不支持项有明确错误和文档，生产配置未切换。

### B5．整站冷切换演练，不默认执行生产切换

1. 暂停新 Task／Loop 投递并等待活动 Loop 全部终结。
2. 备份配置与数据库，记录当前 Runtime、Profile 映射和部署版本。
3. 确认旧 Thread／Checkpoint 的 DeerFlow 只读保留或归档方式。
4. 以 `FLEET_RUNTIME` 切换新部署，执行健康检查、协议自发现和最小任务闭环。
5. 只有在验收签字后恢复新任务；本计划本身不构成生产切换授权。
6. 回滚同样先排干新 Runtime 活动 Loop，再切回原版本；不尝试跨 Runtime 恢复 Checkpoint。

## 4. 总体验收矩阵

| 层 | 必测路径 |
|---|---|
| 协议 | 自发现、operation 白名单、写确认、幂等回放、409／429、human-only |
| GUI | 旧页面读写、Playground、操作助手、二进制下载、SSE（如启用） |
| 生命周期 | 注册／更新／休眠／启动／销毁、Task／Loop／Thread／Run |
| 恢复 | 打回同 Thread、临时故障重试、步数上限恢复、无 checkpoint 能力降级 |
| 观测 | 消息、Run 状态、Token、产物、审计 actor、能力不支持错误 |
| 安全 | 无任意 URL 执行、无系统 PAT 入 Runner、无秘密入 Thread／日志／审计正文 |
| 回滚 | 每个 PR 可单独 revert；冷切换与反向切换均先排干活动 Loop |

## 5. 评审门与暂缓事项

- **G0（当前）**：只评审 ADR 与本拆解，不代表任何功能已实现。
- **G1**：旧 GUI 协议化完成，业务 API 与数据库未变。
- **G2**：DeerFlowAdapter 行为等价，第二套 Harness 垂直切片仅在测试环境通过。
- **G3**：具体 Harness、历史只读方案和冷切换窗口评审完成后，才允许切换演练。

评审需明确接受的默认值：整站单 Runtime、冷切换、旧状态不迁移、Runtime 专属 YAML 不保证移植、第一轮不做 per-Agent 混跑。任何一项要改变，都先新增 ADR 并重新评估工作量。
