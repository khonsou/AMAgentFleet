# 阿里云多 Agent 舰队平台

面向财务、销售等业务场景的多 Agent 运行与管理平台。Agent 执行内核锁定 DeepSeek（OpenAI 兼容协议接入），平台负责 agent 的全生命周期：注册、版本化配置、拉起、休眠、唤醒续跑、销毁、授权与审计。

> **上游与部署**：本项目基于 [DeerFlow](https://github.com/bytedance/deer-flow)（字节跳动开源，MIT）二次开发，执行底座（网关）来自 fork [khonsou/deer-flow](https://github.com/khonsou/deer-flow) tag `am-fleet-base-v1`（上游 `89c9f08` 锁定，零改源码）。生产部署（阿里云 ECS + Compose）见 [deploy/DEPLOY.md](deploy/DEPLOY.md)——运维从该文件入手即可，无需开发环境。

## 当前状态

**阶段**：✅ Spike 0001 已通过（2026-10-08，Go）。✅ Fleet Console v0 已可用。✅ [Spike 0002](docs/spikes/0002-adrs0008-door-validation.md) 已通过（2026-10-09）——ADR-0008 开门验证全过。**阶段**：✅ [Spike 0003](docs/spikes/0003-task-pool-acceptance.md) 已通过（2026-10-09 晚，冷启动回归全绿）——ADR-0010 任务池全链路：task/loop 分离、竞争领取 ≤3、强制验收、打回同 thread 续跑、人机同构交付、runner 工具 agent_spawn/agent_message（零改 DeerFlow 源码）。Console 三件套：Fleet Loop 看板 + 验收台 + task 详情（任何状态可进入/可终止）。

**任务池已落地（ADR-0010，2026-10-09 晚）**：task/loop 分离、竞争领取 ≤3、交付自动捕获、强制验收（采纳/退回/人交付）、打回同 thread 续跑；runner 工具 `agent_spawn`/`agent_message` 经 config.yaml `group: fleet` 挂载（未改 DeerFlow 源码），指挥官可自主派子任务入池。Console 看板已加验收台（交付比较 + 采纳/退回）。

~~任务池模型已立项（ADR-0010，2026-10-09）~~：task=验收单元 / loop=执行单元，领取三途径（人指派/人自领/指挥官认领 cron），同 task ≤3 个并行 loop，验收强制。随 P3 实施（先于/伴随 agent_spawn）。

**P2 主体已落地（2026-10-09）**：`control-plane/`（Registry + Admin API :8003 + Dispatcher + Inbox + Scheduler 薄层）、`agents/` YAML 仓库（首个 agent：应收对账）、Console 升级（Registry agents 区 + agent 详情页配置/对话一页合并，QA-7）。端到端冒烟全过：YAML 部署→网关同步、任务派发 token 落库、Inbox 攒批合并唤醒、cron 对账建任务。**P3 收尾完成（2026-10-09 夜）**：dispatcher 多消费者并行（3 worker，实测 6s vs 串行 40s+）；cron 池化（croniter 触发 → 池任务 → 舰队指挥官认领，防双触发/防补投）；未认领任务池 GUI（横向货架 + 领取/指派/关闭）。**P3 完整收口（2026-10-09 深夜）**：人机认领统一（单代码路径，人=`human`，根治双路线 bug）；人 loop 不计 3-loop 上限 + 人交付框（贴飞书链接进验收台，与 agent 产物同构）；统一认领 UI（🧑/🤖 单一下拉）；URL 自动链接化；会话详情页就地验收；四标签筛选视图（待分配/执行中/待确认/已验收）；批量清理 30 个滞留任务；scheduler sync 返回模型修复；冷启动回归全绿。

**P3b 已落地（2026-10-10）**：FAP v1 第三方 agent 接入面（[ADR-0011](docs/adr/0011-third-party-agent-access-protocol.md)，协议清单 [agent-api.md](docs/design/agent-api.md)）——`GET /api/meta` 免鉴权自发现 + `GET /api/agent-doc` 机器可读文档 + 全响应 `X-Protocol-Version`；写操作 `Idempotency-Key` 幂等（`fap_idempotency_keys` 表 24h 窗口，同键同 actor 回放、跨 actor 409）；per-PAT/IP 令牌桶限速（`429 + retry_after`，60/min 可配）；写操作 `source`/`actor` 自报字段透传 `audit_events.detail`；PAT 鉴权（复用 DeerFlow 原生 `personal_access_tokens` 同库表，scope 仅 read/write，控制面自校验；Console 新增 SETTINGS 页签发/吊销）。参考客户端 `examples/fap-quickstart.mjs`（零依赖 Node≥22，自发现→发起→领取→交付→轮询验收全链路实测通过，含幂等回放 demo）。**边界**：FAP 端点仅内网绑定，公网暴露待 P4 OIDC/PAT 全链路（ADR-0011 第 5 条）；loopback 无 PAT 放行仅限 Console 本机代理。

**待办**：~~fork DeerFlow 锁版本~~ ✅ QA-8 已完成（[khonsou/deer-flow](https://github.com/khonsou/deer-flow) tag `am-fleet-base-v1`，基线 `89c9f08` 已按 blob 哈希验证）；阿里云首次部署实测（ECS 购买后按 DEPLOY.md §4）；P2 规模化验收（10 agent）、P4（OIDC + 成本台账）。

## Fleet Console（`console/`）

管理界面 MVP，供人工测试基本生命周期：

- **Fleet 看板**：总会话/运行中/异常/Token/Checkpoint 概览；会话表（状态、Runs、Checkpoints、LLM 调用、Token、最后活跃）；拉起新 agent（可带首条指令）；销毁（连 checkpoint 一起清除）。
- **会话详情页**：消息流（人类/Agent 气泡，5s 轮询）、同 thread 续跑输入框（断点水合）、Run 生命周期记录（状态/Token/LLM 调用/错误）。
- 架构：`console/backend`（FastAPI:8002，asyncpg 只读 PG + httpx 转发网关）+ `console/frontend`（React+Vite）。
- 启动：`cd console && npm run dev`（`scripts/dev.sh` 依次拉起 PG→网关:8001→后端:8002→前端，退出全回收）。
- 注意：依赖网关 `run_events.backend: db`（spike 期间已从 memory 修正，否则消息只存进程内存，重启即丢）。

## Agent YAML 常用开关（`agents/`）

| 字段 | 语义 |
|------|------|
| `spec.agent.skills` | 三态：`null`（不写值）= 全部已启用技能可见（含对话沉淀）；`["a"]` = 白名单；`[]` = 零技能。**禁止无审查写 `[]`** |
| `spec.agent.allowed_subagents` | 不写（null）= 随 fleet 默认开启 in-thread subagent（dispatcher 统一 `subagent_enabled=true`）；`[]` = 硬拒（该 agent 无 `task` 工具，派 subagent 的诉求会退化为 `agent_spawn` 入池） |
| `spec.agent.tool_groups` | 不写（null）= 全部工具组（含 fleet 的 `agent_spawn`/`agent_message`）；按需白名单收紧 |
| `egress` | 出站域名白名单；留空 = 不放开任何出站 |

## 文档索引

| 文档 | 内容 |
|------|------|
| [MVP 技术方案 v3](deepseek-agent-fleet-mvp-design.md) | 总体设计：需求确认、硅谷实践、开源调研、架构、关键机制、数据模型、开发计划、成本、风险 |
| [docs/adr/](docs/adr/) | 架构决策记录（决策历史与否决理由；与设计文档冲突时以最新 ADR 为准） |
| [docs/spikes/](docs/spikes/) | 验证性实验：0001 DeerFlow 生命周期 ✅ / 0002 ADR-0008 开门验证 ✅ / 0003 任务池验收 ✅ |
| [docs/design/agent-api.md](docs/design/agent-api.md) | 功能清单（操作→API→现状，v0.1.0 基线）与第三方 agent 接入协议 FAP v1（自发现/幂等/审计透传，ADR-0011） |
| [docs/dev/p3b-fap-kickoff.md](docs/dev/p3b-fap-kickoff.md) | **P3b 开工说明（给 coder）**：FAP 接入面任务分解、现状资产地图、验收标准 |
| [docs/dev/p2-scale-acceptance.md](docs/dev/p2-scale-acceptance.md) | **P2 规模化验收测试计划**：10 agent 注册、cron 准时、休眠/唤醒全链路 |
| [AGENTS.md](AGENTS.md) | 仓库约定：目录结构、安全红线、开发规范（人类与 AI 编码助手共用） |
| [deploy/DEPLOY.md](deploy/DEPLOY.md) | **生产部署手顺**（运维入口）：上游关系、compose、验收 checklist、2-ECS/RDS 变体、安全红线 |
| [RELEASE.md](RELEASE.md) | **发布说明**（运维入口）：仓库地图、按 tag 发版、底座升级、回滚、发布前检查清单 |

## ADR 一览

| # | 决策 | 状态 |
|---|------|------|
| [0000](docs/adr/0000-record-architecture-decisions.md) | 使用 ADR 记录架构决策 | 已接受 |
| [0001](docs/adr/0001-agent-as-config-stateless-runners.md) | Agent 即配置，runner 无状态，状态外置 checkpointer | 已接受 |
| [0002](docs/adr/0002-harness-base-deerflow-spike-gate.md) | Harness 以 DeerFlow 2.0 为底座（spike 门），备选自建薄 runner | 已接受（Spike 0001 通过） |
| [0003](docs/adr/0003-deepseek-official-api-model-lock.md) | 模型锁定 DeepSeek 官方 API，OpenAI 兼容协议 | 已接受 |
| [0004](docs/adr/0004-multi-agent-thread-tree-summary-only.md) | 多 agent 协作：线程树 + 强制摘要返回 | 已接受 |
| [0005](docs/adr/0005-token-vault-gateway-side-injection.md) | Token Vault + 网关侧令牌注入，不建审批流 | 已接受 |
| [0006](docs/adr/0006-mvp-infra-two-ecs-compose.md) | MVP 基础设施：2 台 ECS + Docker Compose | 已接受 |
| [0007](docs/adr/0007-rejected-alternatives-ecosystem-survey.md) | 不采用的项目及理由（生态调研结论） | 已接受 |
| [0008](docs/adr/0008-reuse-deerflow-agents-api-for-per-agent-config.md) | per-agent 配置层复用 DeerFlow agents_api，Registry 降级为组织级包装 | 已接受（方向，P2 开门验证） |
| [0009](docs/adr/0009-model-policy-provider-lock-profile-free.md) | 模型政策放宽：provider 锁定 + per-agent profile 自由 | 已接受 |
| [0010](docs/adr/0010-task-loop-pool-mandatory-acceptance.md) | 任务池：task/loop 分离、竞争领取（≤3）、强制验收 | 已接受 |
| [0011](docs/adr/0011-third-party-agent-access-protocol.md) | 第三方 agent 接入协议：自发现 + 单一能力面 + 复用任务池验收 | 已接受（提案，P3b 实施） |

## 关键输入（已确认的 QA 结论）

- 并发峰值约 10 个 agent；注册规模目标数千。
- DeepSeek 官方 API key 已就位；不自建 GPU。
- 触发方式：Web 对话（公网，出差可用）/ IM / 定时 / agent 唤醒 agent。
- 授权：业务系统 OAuth，普通 agent 用员工授权（OBO），超级 agent 用 M2M 服务账号。
- 弱人工审批需求；纯内部使用，无等保/出境约束。

## 下一步

~~第一步（本周，1 人 3–5 天）~~：✅ [Spike 0001](docs/spikes/0001-deerflow-basic-lifecycle.md) 已完成（Go）；✅ Fleet Console v0 已可用（原 P4 内容提前，见上节）。

**P2 阶段（主体已落地 2026-10-09）**：~~前置任务 fork DeerFlow 仓库锁版本（QA-8）~~ ✅（[khonsou/deer-flow](https://github.com/khonsou/deer-flow) `am-fleet-base-v1`，生产 compose 按 tag 构建）；~~ADR-0008 开门验证~~ ✅（[Spike 0002](docs/spikes/0002-adrs0008-door-validation.md)）；Agent Registry（组织级包装层）+ Admin API + YAML 版本化 ✅；Dispatcher + 队列 + Inbox ✅（队列 PG 实现，Redis Stream 为部署期替换）；Scheduler(cron) ✅（薄层对账，不自建触发引擎）；Console agent 详情页"配置 + 对话"一页 ✅（QA-7）。~~P2 规模化验收~~ ✅（2026-10-10，[docs/dev/p2-scale-acceptance.md](docs/dev/p2-scale-acceptance.md)：10 agent 批量注册生命周期、cron 节奏 120.15s±、休眠/唤醒全链路，脚本 `scripts/p2-scale-acceptance.py` 可重放；途中修复休眠唤醒穿透与销毁 FK 两个 bug）。**剩余**：§12 第 1–4 条 QA（IM 选型、IdP、M2M 清单、首批 agent 清单，分别阻塞 P3/P4）。详见设计文档 §9。
