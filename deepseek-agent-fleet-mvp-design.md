# 阿里云多 Agent 舰队平台 —— MVP 技术方案 v3

> **文档状态**：已立项 · 正式设计文档
> **版本**：v3（2026-10-08）
> **关联文档**：[架构决策记录（ADR）](docs/adr/) · [项目索引](README.md) · [仓库约定](AGENTS.md)
> **当前阶段**：设计冻结，待执行第一步最小验证 [Spike 0001](docs/spikes/0001-deerflow-basic-lifecycle.md)（ADR-0002 决策门）

> 目标：在阿里云上运行一个可对数千个 Agent 做全生命周期管理（注册、配置、拉起、休眠、恢复、销毁、授权、审计）的平台；Agent 执行内核锁定 DeepSeek，面向财务、销售等业务场景高度定制；Agent 本身不沉淀业务数据，通过受控工具访问公司现有系统。

---

## 1. 需求确认（QA 结论 → 设计影响）

| # | 结论 | 对设计的影响 |
|---|------|--------------|
| 1 | 公司规模小，**同时运行 ~10 个 agent 足够** | runner 池做固定小规模（10–12 个容器），MVP 不需要弹性伸缩组件；基础设施成本骤降 |
| 2 | **已有 DeepSeek API key**，runtime 在阿里云机房即认为安全，不自建 GPU | 模型层 = DeepSeek 官方 OpenAI 兼容端点；无 GPU、无百炼依赖；数据出域问题关闭 |
| 3 | 触发 = 定时 + IM + **agent 唤醒 agent**；每个 agent 要一个 **Web 对话页**（fleet view 子页面） | 统一触发模型：Web/IM/cron/agent 消息全部归一为"任务"进 Dispatcher；Console 成为一等组件；内置 `agent_message` 工具 |
| 4 | 业务系统走 **OAuth**：普通 agent 用**人类员工的授权（OBO）**；超级 agent 若系统支持 **M2M** 则用超级账号 | **Token Vault** 组件：托管员工 OAuth token、按需签发短期访问令牌；M2M client credentials 单独管理 |
| 5 | **人工审批需求弱**：agent 以分析、建议、规划 todo、跟踪执行为主 | 砍掉审批台；MCP Gateway 保留白名单与审计；工具默认只读优先，写操作收敛至少数工具 |
| 6 | 纯内部使用，无合规硬约束 | 移除等保/出境评估章节 |
| 7 | Console 需**公网可达**（出差场景），OAuth 登录 | 控制台/API 经公网入口 + OIDC/OAuth SSO；agent runtime 与业务系统仍留在 VPC 内 |
| 8 | **多 agent 协作是刚需**；现有"单 agent 多 skill"系统已出现上下文溢出、记忆混乱 | 核心章节：多专精 agent + 线程树 + 摘要返回 + 上下文纪律（见 §7.3、§7.4） |
| 9 | **全公司可见所有 agent**（统一舰队，无部门/个人隔离；2026-10-09 QA-5） | agents_api user-scoped 隔离不适用，映射简化为单一共享组织作用域（如统一 `org` 用户）；fleet 看板无需权限过滤；审计仍记操作人 |
| 10 | **Agent 定义与运行实例均可选模型**（2026-10-09 QA-6；2026-10-28 增补） | Agent YAML 的 `model` 是默认值，Loop／Thread 创建时可覆盖；用户可让指挥官自动选择 Subagent 模型或固定继承。Profile 限 Gateway 已登记目录；多模型与独立 API Key 复用 Managed Models，密钥不进协议和 Thread。见 ADR-0009／0028 |
| 11 | 触发器排期：**cron 先行（P2），IM 靠后**；每个 agent 必须有一个网页同时看到**配置与对话**（2026-10-09 QA-7） | P2 只做 Scheduler(cron)；IM adapter 排 P3 之后；Console agent 详情页 = 配置区 + 对话区一页合并（P2 验收项） |
| 12 | **DeerFlow 跟踪策略 = fork GitHub 仓库**（2026-10-09 QA-8） | P2 前置任务：fork 并锁定版本基线，定制以 patch/cherry-pick 管理；替换 spike 的 tarball 快照 |

---

## 2. 硅谷最新实践（2025–2026）速览

### 2.1 Anthropic：fleet 管理与上下文纪律

- **Agent = 一份声明式配置**（YAML：system prompt、模型、工具权限），不是常驻进程；fleet view 统一查看所有会话、派发、attach；supervisor 托管会话。
- **四个扩展原语**：MCP servers、Skills、Sub-agents、Hooks；hooks（确定性校验代码）是最具规模化能力的抽象。
- **上下文纪律（直接对治 QA 8 的痛点）**：子 agent 只把**摘要**返回父上下文，全量 transcript 存 sidechain 文件用于审计——"summary-only return"是刻意的上下文保护设计。Claude Code agent teams 的 token 消耗约为单会话 7 倍，编排要克制。

### 2.2 OpenAI：harness 与 compute 分离 + Durable Execution

- 2026 年 4 月 Agents SDK 改版核心：**harness 与执行容器分离**，状态外置，snapshotting + rehydration——容器丢了在新容器里从检查点继续。
- 默认假设 prompt injection 会发生：凭证不进模型生成代码的执行环境。
- 长任务可靠性交给 durable execution 引擎（Temporal / DBOS / Restate），不自造；AgentKit 可视化编排器已弃用，生产编排收敛到 **code-first**。

### 2.3 行业共识的四条判断

1. **会话记忆 ≠ 持久执行**：执行日志 + 幂等工具边界 + 版本化 prompt/工具，崩溃后不重复副作用地恢复。
2. **LangGraph Postgres Checkpointer 是状态持久化事实标准**；注意许可证：库 MIT，官方服务层 `langgraph-api` 是 Elastic License 2.0 且自托管生产需商业授权——所以要么自写服务层，要么用开源替代品（见 §3 的 DeerFlow）。
3. **安全四边界**：独立沙箱、默认拒绝出口、租户级隔离、**单次运行级短期凭证**。主导失败模式是凭证过大 + 出口开放。agent 持员工 OAuth token 的场景下权重更高。
4. **注册数 ≠ 并发数**：agent 无状态化、状态外置、按需水合。10 并发的量级下固定小池即可。

---

## 3. GitHub 开源生态调研：不闭门造车

调研了 GitHub `agent-fleet` / `control-plane` 等 topic 下约 40 个项目及国内外生态，按我们架构的层次分类：

### 3.1 项目分层地图

| 层 | 项目 | 是什么 | 对我们的价值 |
|----|------|--------|--------------|
| **控制平面（最接近）** | **openshift-online/agent-control-plane (ACP)**，Red Hat，MIT | K8s 上跑 agent 的多租户平台：YAML GitOps 定义 agent、**凭证 sidecar 掩码（agent 永远看不到原始 token）**、cron 定时会话、OIDC SSO、RBAC、PG 数据模型（Project/Agent/Session/**Inbox**/Credential/ScheduledSession） | **设计蓝本**。数据模型和"凭证不出 sidecar"模式直接照搬。但整仓部署不推荐：OpenShift/Keycloak/Kustomize 重依赖、面向 coding agent（Claude Code/Gemini CLI bridge 是 production，LangGraph bridge 还是 stub）、单机小团队跑不动这套 |
| **Harness/网关（可直接用）** | **bytedance/deer-flow (DeerFlow 2.0)**，83.5k star，MIT | LangGraph 生态的 SuperAgent harness：subagents、skills、长期记忆、沙箱、**IM 频道（Telegram/Slack/飞书）**、**cron 定时任务**、**DeepSeek 官方端点一等适配**（含 reasoning 保留）、Postgres checkpointer、**多 worker 网关 + Redis 流桥 + run 租约恢复（崩溃接管）**、Langfuse 追踪、Helm 部署 | **解决 §2.3 第 2 条的许可证缺口**：它就是一套 MIT 的、生产化的 LangGraph 服务层，且 DeepSeek 原生支持。W1–W2 的自研量大幅压缩 |
| 协作/记忆治理 | Caura 系列 demo、Bernstein（Apache-2.0，声明式治理 + 可重放记录） | 多 agent 共享记忆的边界强制、约束传播、可验证审计 | 二期参考。MVP 用线程树 + 摘要返回已够 |
| coding agent fleet | amux、Warren、Multica（43k star）、ruflo（67k star）、Conductor、Superset 等 | 并行跑 coding agent：worktree 隔离、共享任务板、自愈、手机端指挥 | **领域不符**（面向代码/PR，不是业务系统），但模式可借鉴：worktree 隔离 ≈ 我们的 thread 隔离；supervisor 托管 ≈ Dispatcher |
| MCP 治理 | control-plane topic 下的开源 MCP gateway（TS）、"action firewall"（Go） | 治理 agent 可用工具、调用审计、策略拦截 | MCP Gateway 层可选型时评估，能省自研 |
| 领域参考 | **TauricResearch/TradingAgents**（110k star，Apache-2.0） | 金融多角色 agent：基本面/情绪/技术分析师 + 研究员辩论 + 交易员 + 风控 | 不是基建，是**财务 agent 角色拆分的领域参考**——设计首批 agent 职责清单时值得读 |
| 国内平台 | Dify、FastGPT、DB-GPT | 低代码 LLM 应用平台 | 不匹配：code-first 决策 + 每 agent YAML + 深度定制，低代码画布反而碍事 |

### 3.2 三个多代理项目的硬数据对比（GitHub API 实时拉取，2026-10-08）

| 维度 | **ACP**（Red Hat，openshift-online） | **DeerFlow 2.0**（字节跳动） | **TradingAgents**（TauricResearch） |
|------|--------------------------------------|------------------------------|-------------------------------------|
| 定位 | agent 舰队**控制平面**（平台） | SuperAgent **harness + 网关**（产品） | 金融交易多角色**框架**（库/demo） |
| Star / Fork | **12 / 13** ⚠️ | **83.5k / 11.6k** | **110k / 21.2k** |
| 最近提交 | 2026-10-05（活跃） | 2026-10-08（当天，极活跃） | 2026-10-03（活跃） |
| 发布 | **无 release** ⚠️ | v2.1.0（2026-09-24），节奏稳定 | v0.6.0（2026-10-03） |
| 贡献者 | ~55（大概率多为 Red Hat 内部） | ~523 | ~24 |
| 代码规模（GitHub 语言统计） | Go 2.7M + Python 1.6M + TS 1.3M ≈ **6.4 MB**，6+ 个服务组件 | Python 29M + TS 5.5M ≈ **35 MB**，单体网关 + 前端 | Python **0.99 MB**，单包 |
| 部署需求 | **最重**：K8s（OpenShift 4.22+ 全功能）+ Keycloak + cert-manager + Gateway API + OpenShell 沙箱控制器 + PG + Kustomize；本地演示也要 Kind/CRC 16GB | **适中**：`make setup` 向导 + Docker Compose 一把起；4C8G 起步、8C16G 建议；SQLite 默认，多 worker 才要 PG+Redis；有 Helm chart | **最轻**：pip/conda 装完跑 CLI；无服务端、无 DB、无鉴权 |
| 40 人公司适配 | ❌ 平台本身比业务重，且 12 star/无 release，**只能当蓝本不能当依赖** | ✅ 当**产品**部署用（能配置不改代码），定制只碰协作语义边缘 | ✅ 但用途是**抄角色设计**，不进基建 |

**关键结论：这三个不是同一层的东西，"选最轻的"是伪命题。** TradingAgents 轻但解决不了 fleet 管理；ACP 重且成熟度低；DeerFlow 居中且恰好填 LangGraph 服务层的许可证缺口。40 人公司的轻量化原则应该是：**不养平台，只养一层薄控制面**——底座选"部署简单 + 社区活跃"的，定制收敛在 2–3k 行自有代码里。

### 3.3 结论：没有完全对口的现成项目，但有两个可以站在上面

**没有一个项目同时覆盖"业务 agent fleet + DeepSeek harness + OBO OAuth + 阿里云小规模部署"**——coding agent fleet 类项目领域不符，ACP 太重且面向 coding，Dify 类是低代码路线。但分层看，两层可以不平地起楼：

**推荐策略：DeerFlow 2.0 做 harness/网关底座 + 自建薄控制面（借鉴 ACP 数据模型）**

```
我们自建的（薄，~2-3k 行）:           直接复用的（厚，现成的）:
├─ Agent Registry (YAML+PG)          ├─ DeerFlow 2.0 (MIT):
├─ Dispatcher + 统一触发模型          │   ├─ LangGraph 服务层 + PG checkpointer
├─ Token Vault (OBO+M2M)             │   ├─ DeepSeek 官方适配器
│   (借鉴 ACP 凭证 sidecar 模式)      │   ├─ 多 worker + 租约崩溃恢复
├─ MCP Gateway + 工具白名单           │   ├─ IM 频道框架 (飞书可参考)
├─ agent_message / agent_spawn       │   ├─ cron 定时任务
│   (线程树+摘要返回, 定制点)         │   ├─ skills 系统
└─ Console (fleet 列表+对话子页)      │   └─ Langfuse/SLS 追踪
    (ACP 的 Inbox/Session 模型照搬)   └─ Langfuse (自托管)
```

**为什么不直接部署 ACP**：除技术栈绑定外，硬数据也不支持——12 star、无正式 release、6.4MB 多服务代码、要求 OpenShift 生态，对 2 台 ECS 的 40 人公司来说平台本身比业务还重。**但它的数据模型 spec 和凭证隔离设计值得逐条抄**——尤其 `Inbox`（挂在 agent 上的持久消息队列，跨 session 存活，正好实现"休眠 agent 被消息唤醒"）和 `ScheduledSession`（cron + 重叠策略 + 时区）。定位为"蓝本"，不是依赖。

**为什么 DeerFlow 而不是纯自建 runner**：§2.3 的许可证坑本要我们自写 LangGraph 服务层（队列、持久化、流式、崩溃恢复——每一项都是真实工作量）；DeerFlow 2.0 恰好把这套做完了且 MIT、DeepSeek 原生适配、还带 IM 频道和 cron。风险是代码体量不小，需要 1–2 天读代码确认定制点（线程树语义、per-agent 配置隔离）是否好改——**W1 先用 3 天做 spike 验证再定**。

**备选（Option B）**：若 spike 发现 DeerFlow 定制成本高于预期，退回纯自建薄 runner（LangGraph MIT 库 + FastAPI，约 200–400 行核心），数据模型仍抄 ACP。两条路的控制面代码不变，风险隔离在 harness 层。

---

## 4. 核心设计判断

| # | 判断 | 理由 |
|---|------|------|
| 1 | **Agent 是"配置 + 状态指针"，不是常驻服务** | 10 并发用固定小 runner 池；注册数涨到几千也不改架构 |
| 2 | **从"单 agent 多 skill"转向"多专精 agent + 受控协作"** | 你们已实测前者上下文溢出/记忆混乱；硅谷实践同样证明隔离 + 摘要传递是唯一可持续解 |
| 3 | **Harness 站在 DeerFlow 2.0 上，模型锁定 DeepSeek** | MIT、DeepSeek 原生适配、checkpoint/IM/cron 现成；锁定的是模型与协作语义，不锁框架代码 |
| 4 | **业务数据零落地** | 平台只存会话状态 + 审计；业务数据以源系统为准、实时取数 |
| 5 | **授权一等公民，形式是"凭证托管 + 白名单 + 审计"，不是审批流** | 弱审批需求下，安全靠 OAuth scope 最小化、短期 token、全量审计兜底 |
| 6 | **code-first，YAML 声明式配置 + Git 版本化** | OpenAI 弃用画布的信号 + 小团队，配置文件即管理界面 |

---

## 5. 总体架构

### 5.1 架构图

```
人 (公网, OAuth 登录)                定时 (cron in YAML)        IM (钉钉/企微 webhook)
        │                                  │                          │
        ▼                                  ▼                          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 公网入口 (SLB/ALB + TLS)                                                      │
│  ├─ Console (Web): Fleet 列表页 + 每 agent 对话页(流式) + 审计查看             │
│  └─ Admin/Chat API (FastAPI): OIDC 鉴权                                      │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │ 统一归一为 Task(agent_id, input, source, parent_thread?)
┌───────────────────────────────▼─────────────────────────────────────────────┐
│ 控制平面 (VPC)                                                                │
│  Dispatcher → 任务队列 (Redis Stream)                                         │
│  Agent Registry (RDS PG): agent 定义/配置版本/状态/Inbox(借鉴ACP)              │
│  Token Vault: 员工 OAuth token 托管(KMS 加密) + 短期令牌签发                   │
│  Scheduler: 解析各 agent YAML 的 cron，定时投递任务                            │
│  审计: 追加写 audit_events → OSS/SLS 归档                                     │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │ 派发 (agent_id, thread_id, 配置版本)
┌───────────────────────────────▼─────────────────────────────────────────────┐
│ 数据平面 (VPC, 2 台 ECS Docker Compose 或 2 节点 ACK, 固定 10–12 runner)       │
│                                                                             │
│  Agent Runner (无状态容器, 基于 DeerFlow 2.0 定制):                            │
│   1. 拉配置(YAML 指定版本) + skills + 向 Vault 换本次运行短期令牌              │
│   2. 按 thread_id 从 PG Checkpointer 水合状态                                 │
│   3. DeepSeek agent 循环 (LangGraph + MCP client)                            │
│   4. 每步 checkpoint 回写；结束/空闲超时 → 容器释放回池                        │
│                                                                             │
│   内置协作工具: agent_message / agent_spawn ──► 回 Dispatcher 生成子任务        │
│                                                                             │
│        │ DeepSeek API        │ MCP Toolbox                │ 状态与产物        │
│        ▼                     ▼                            ▼                 │
│   api.deepseek.com     ├─ 财务系统 MCP Server        RDS PG: checkpoint      │
│   (已有 key, TLS)      ├─ 销售/CRM MCP Server        Redis: 队列/热缓存       │
│                        ├─ OA/IM MCP Server           OSS: 产物/审计归档       │
│                        └─ 通用工具(搜索/计算)                                 │
│                                                                             │
│  可观测: OpenTelemetry → SLS；Langfuse(自托管) trace/token/成本归账            │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 5.2 组件选型

| 层 | 组件 | MVP 选型 | 来源/备选 |
|----|------|----------|-----------|
| 模型 | DeepSeek | **官方 api.deepseek.com + 已有 key** | — |
| Harness | Agent 循环+服务层 | **DeerFlow 2.0 (MIT) 定制**（checkpoint、IM、cron、skills、崩溃恢复现成） | Option B：LangGraph MIT 库 + FastAPI 自建薄 runner |
| 持久化 | 状态 | **RDS PostgreSQL 基础版**：checkpointer + registry + 审计共用 | Redis 热层 |
| 队列 | 调度 | **Redis Stream** | RocketMQ |
| 计算 | 运行时 | **2 台 ECS (4C8G) Docker Compose**（或 2 节点 ACK），固定 10–12 runner | 并发 >50 再上 KEDA |
| 工具 | 业务接入 | **MCP Server 每系统一个** + MCP Gateway（可评估 control-plane topic 下的开源 gateway） | — |
| 协作 | agent 互调 | **内置 `agent_message` / `agent_spawn`**，走 Dispatcher（异步、可审计、线程树） | 二期共享任务板（参考 Multica） |
| Skills | 能力包 | DeerFlow skills 机制 + **OSS 版本化**，按需加载 | — |
| 授权 | 身份 | **Token Vault**（OBO + M2M，借鉴 ACP 凭证 sidecar：令牌在网关侧注入，runner 不持久持有）+ 工具白名单 | OPA |
| 入口 | 人 | **公网 ALB + OIDC SSO** 的 Console | 钉钉工作台免登 |
| 观测 | 追踪 | **Langfuse 自托管**（DeerFlow 原生支持）+ OTel → SLS | — |
| 蓝图 | 数据模型 | **照搬 ACP spec**：Project/Agent/Session/Inbox/Credential/ScheduledSession | — |

ACK 仅增加测试部署路径（ADR-0012），不改变 ADR-0006 的生产默认 ECS + Compose；当前 ACK 部署为单副本验证形态。

---

## 6. Agent 定义与生命周期

### 6.1 声明式 YAML

```yaml
apiVersion: agentfleet/v1
kind: Agent
metadata:
  name: ar-reconciliation-01        # 应收对账 agent
  owner: finance-team
spec:
  model:
    name: deepseek-default          # 稳定 Profile；官方模型 ID 只在 Gateway Profile 中维护
    temperature: 0.1
    maxTokensPerRun: 200000         # 单次任务 token 预算，超了熔断
  skills:                           # 按需加载
    - name: reconciliation
      version: 1.4.2
  tools:
    allow:                          # MCP 工具白名单（最小权限）
      - erp.ar.query
      - erp.voucher.draft
      - fleet.agent_message         # 内置协作工具
    deny:
      - erp.voucher.post
  identity:
    mode: obo                       # obo=以发起人员工身份; m2m=超级服务账号
    allowedCallers: ["zhangsan", "finance-team"]
  triggers:
    cron:
      - cron: "37 8 * * 1-5"        # 每工作日 08:37（错峰分钟）
        prompt: 检查今日应收异常并输出摘要
        # orchestrator: fleet-cmd-01  # 可选；缺省 = 所属 Agent 直接执行（ADR-0016）
    im: true
    web: true
    agentCallable: true             # 允许被其他 agent 唤醒
  budget:
    dailyTokenCap: 2000000
  persistence:
    checkpointTtlDays: 90
    idleHibernateMinutes: 30
```

**配置版本化**：每次修改产生新版本；运行中任务锁定旧版跑完，新任务用新版。

### 6.2 生命周期状态机

```
REGISTERED ──任务到达(cron/IM/web/agent)──► RUNNING ──空闲30min──► HIBERNATED
                                              │  ▲                    │
                                              │  └──新消息进 Inbox─────┘
                                              │     新容器按 thread_id 水合续跑
                                              ▼
                                   TERMINATED (任务完成/熔断/人工销毁)
                                              │
                                              ▼
                                   ARCHIVED (checkpoint+审计归档 OSS)
```

- **休眠**：checkpointer 每步已落库，"休眠"只是释放容器回池。
- **唤醒语义照搬 ACP Inbox**：消息投递到挂在 agent 上的持久 Inbox（PG 表，跨 session 存活），Dispatcher 发现有待处理消息即拉起 runner 水合续跑。
- **10 并发的现实**：固定 12 个 runner 容器常驻，冷启动 <2s，无需弹性组件。

---

## 7. 关键机制设计

### 7.3 多 Agent 协作模型（对治"单 agent 多 skill"的坑）

问题本质：**一个上下文装了太多职责**。解法做成平台级能力：

**线程树（Thread Tree）**

```
thread#101 [销售分析 agent]  "分析 Q3 大客户流失"
   │ agent_spawn(finance-agent, "取这5家客户的回款数据")
   ▼
thread#102 [财务 agent]  ──► 执行 ──► 返回【摘要 ≤500字】给 thread#101
   (完整过程留在自己的 checkpoint，不污染父上下文)
```

- **两个内置工具**：`agent_spawn(agent_name, task, context_hint)` 创建子线程、阻塞等摘要；`agent_message(agent_name, content)` 异步唤醒不等结果（进对方 Inbox）。
- **summary-only 是强制的**：子 agent 返回经压缩步骤（DeepSeek 摘要 + 长度硬上限），全量 transcript 只存子线程 checkpoint，审计时沿 thread 树下钻。
- **防循环**：线程树深度上限（默认 3）+ 协作链 token 总预算；A→B→A 的环由 Dispatcher 拒绝并告警。
- **协作审计**：每次 spawn/message 进 audit_events。

### 7.4 上下文工程纪律（平台级强制）

1. **一 agent 一窄职责**：注册强制填 `owner` 与职责描述。
2. **一个 agent loop 一 thread**：cron/IM/agent 触发的每个 agent loop 起跑时绑定新 thread；同一 task 可有多个竞争 loop，退回修正复用该 loop 的同一 thread；人的 web 对话允许同 thread 延续（超 50 轮建议归档重开）。
3. **Skills 按需加载**：只注入 YAML 声明的 skill；skill 内部触发条件不命中不注入正文。
4. **上下文卫生**：DeerFlow 自带 manual context compaction，平台侧再加自动压缩中间件——超阈值历史自动摘要折叠、工具结果落地 OSS 只留引用；checkpoint 按 TTL 清理 + 归档。
5. **预算即护栏**：`maxTokensPerRun` 熔断 + 协作链总预算。

### 7.5 持久化边界

| 数据 | 存哪 | 说明 |
|------|------|------|
| 会话/执行状态 | RDS PG checkpointer | 消息、中间态、图位置；TTL 90 天 |
| 审计事件 | PG 热表 → OSS/SLS 归档 | 触发源、工具调用、参数摘要、token 用量、协作链 |
| 产物文件 | OSS | 报表等输出，生命周期规则 |
| OAuth 凭据 | Token Vault（KMS 加密列） | 员工 refresh token、M2M secret |
| ~~业务数据~~ | **不存** | 以源系统为准，MCP 实时取数 |

### 7.6 授权：OAuth OBO + Token Vault + M2M（借鉴 ACP 凭证隔离）

```
员工首次使用:
  Console 点"授权 ERP" → 跳转业务系统 OAuth → refresh token 存 Vault (KMS 加密)

Agent 运行时 (ACP 模式: 凭证不落地在 agent 侧):
  runner 调 MCP 工具 → 请求只带 (agent_id, thread_id, 发起人身份)
  → MCP Gateway 侧用 Vault 里对应员工的 refresh token 换短期 access token
  → Gateway 直接以该身份调业务系统, 令牌全程不进 runner 上下文

超级 Agent (identity.mode=m2m):
  仅对支持 M2M 的系统, client credentials 存在 Vault
  YAML 需 owner 显式确认; Console 单独标识"超级账号"

每次调用: 校验 YAML 工具白名单 + OBO 身份与 allowedCallers 匹配 + 写审计
```

相比 v2 的重要改进（抄 ACP 的 credential masking）：**短期令牌在 Gateway 侧签发和使用，根本不注入 runner 环境**——被 prompt 注入的 agent 也拿不到原始 token，只能发起"白名单内的调用意图"。这把"凭证过大 + 出口开放"这对最大风险从源头上拆掉了一半。叠加 runner **默认拒绝出口**（仅 DeepSeek API、MCP Gateway、内网白名单）与**只读优先**的工具上架策略。

> 仍需坦诚提示：即便令牌不出网关，被注入的 agent 仍可在员工权限内"发起"白名单内的调用。缓解靠白名单最小化 + 全量审计 + 写操作工具单独评审上架；建议"agent 可执行的写操作清单"作为治理事项定期过一遍。

### 7.7 触发与入口统一模型

| 来源 | 路径 | 说明 |
|------|------|------|
| Web 对话 | Console → Chat API（SSE 流式） | 每 agent 一个对话页（fleet 子页）；同 thread 延续 |
| IM | 钉钉/企微 webhook → Adapter → Dispatcher | DeerFlow 的 IM 频道框架可参考（原生支持飞书）；回复经 IM MCP 回流 |
| 定时 | Scheduler 读 YAML cron → Task/Loop → Dispatcher | 默认所属 Agent 执行；显式 `orchestrator` 才交编排者（ADR-0016） |
| Agent 唤醒 | runner 调 `agent_message` → 目标 agent 的 Inbox → Dispatcher | 见 §7.3 |

### 7.7+ 任务池与验收模型（2026-10-09 修订，ADR-0010）

**派发 ≠ 执行**。所有触发（Web/IM/cron/agent）统一入池，task 是验收单元、loop 是执行单元：

```
task（open → review → done，只前进不回退）
 ├─ loop₁（agent A 领）→ 交付 output₁ ─┐
 ├─ loop₂（agent B 领）→ 交付 output₂ ─┼→ 验收（强制）：采纳 → done
 └─ loop₃（人 领）→ 交付 output₃ ──────┘   （其余 output 存档；打回只打 loop）
```

- **领取竞争非独占**：同一 task 最多 3 个并行 **agent** loop（human loop 不计上限——不烧平台 token；不同 agent/人同台解题，验收并排比较）；
  验收不采纳即自然终止。领取途径仅三种：人指派 / 人自领 / cron 默认由所属 Agent 认领（显式配置可交编排者，ADR-0016）——无暗路径。
- **交付自动**：loop 的 run 成功即捕获 deliverable（摘要 + 文件链接 + thread 引用）。
- **强制验收**：任一 loop 交付 task 即入 review，必须经验收动作（采纳/保持开放/关闭）才终结；
  任何已识别的人或 Agent 均可采纳/退回（含 FAP/A2A + PAT 第三方 Agent，ADR-0015）；
  采纳记审计。打回 = 对该 loop 评论 → 同 thread 水合续跑 → 交 v2，不影响 task 与其他 loop。
- **人机同构**：人领取后在 Console 对话交付，产出结构与 agent loop 一致。
- 数据模型：`fleet_task_loops`（loop 表）、`fleet_task_comments`；`fleet_tasks` 状态收敛 open|review|done。

### 7.7++ Thread 实例选模（2026-10-28，ADR-0028）

- **Agent 定义只给默认值**：`spec.agent.model` 不再是一次执行的强制绑定；每个 agent Loop 领取时解析 `model_profile` 并保存模型快照，Dispatcher 通过 Gateway 原生 `context.model_name` 执行，打回续跑沿用 Loop Profile，快照记录不被改写。
- **人与指挥官分工**：创建 Task 时可指定初始 Thread 的 Profile；`subagent_model_policy=auto` 允许指挥官逐个子任务选模，`inherit` 固定所有后代继承用户指定 Profile。Ops／第三方 Agent 使用同一结构化字段，自然语言不是权限边界。
- **解析与校验**：实例显式 Profile → Agent 默认 → Gateway 默认。可选 `model_policy.allowed_profiles` 约束 Agent 可用范围；未知、越权或继承冲突在入队前拒绝。
- **模型与密钥管理**：复用 Gateway Managed Models，每个受管 Profile 可拥有独立 API Key；现有 `$DEEPSEEK_API_KEY` 继续作为系统默认。密钥只写不回显，管理操作 human-only，不进 Agent Thread、审计或 Git。
- **稳定 Profile 与本次升级（2026-10-31）**：`deepseek-default` 为系统默认，`model=deepseek-flash`；`deepseek-chat` 仅为历史 Task／Loop 兼容别名并指向同一模型，旧实例结束后删除。Agent YAML、脚本和 Console 新 Agent 默认值只引用稳定 Profile。兼容期内两个 Profile 的 `model` 必须同步；移除别名后每套配置只维护 `deepseek-default` 一处。本次 Profile 改名还须把既有 Agent 的 YAML 显式全量同步到 Gateway；启动只自动同步系统 Ops 与指挥官，以后仅改 Profile 的 Provider 模型 ID 不需逐 Agent 同步。
- **同 Key 升级 SOP**：官方同 ID 服务端升级零项目配置；ID 变化时只改 `deploy/config.fleet.yaml` 与本地 `spike/deer-flow/config.yaml` 的 Profile `model`，重新部署／重启网关并做对话、工具调用、续跑、token／延迟冒烟。Managed Models 可建临时 Profile 灰度，编辑时 Key 留空保留。
- **活跃 Loop 约束**：Dispatcher 执行只传 Profile 名，模型快照用于审计而非 Provider 版本锁；切换 Profile 映射会影响后续续跑，活跃 Loop 需先结束或明确接受热升级。

### 7.8 Console 页面（fleet view 极简版）

- **Fleet 列表页**：状态（RUNNING/HIBERNATED/TERMINATED）、今日任务数、今日 token、预算水位；启停、看 YAML 当前版本。
- **Agent 子页（对话页）**：流式对话窗 + thread 列表（可回放轨迹）+ 授权状态。
- **审计页**：按 agent/员工/时间过滤的调用流水。
- FastAPI 后端 + React 单页；第一天就公网 + OIDC。

### 7.9 共享池并发与复用语义（2026-10-15 增补，ADR-0012/0013/0014；验收权限由 ADR-0015 更新）

多用户并发、know-how 共享、task output 复用的完整设计（QA-13~24 决议）见
[docs/design/shared-pool-concurrency.md](docs/design/shared-pool-concurrency.md)。
要点：认领事务化 + 认领者唯一 + 控制面单例门闩（ADR-0012）；任何已识别的
人或 Agent 同权采纳/退回（ADR-0015）；know-how 谱系 lineage + 记忆维持
per-agent 共享 + PAT 绑定员工（ADR-0013）；任务池升级为已验收答案库，
task_search 只读检索 + 引用型交付 + 无失效概念（ADR-0014）。
ADR-0010"3 个并行 loop"措辞澄清为"终身累计 ≤3 个 agent loop"
（QA-13，以 ADR-0012 为准）。

### 7.10 系统 Fleet 操作 Agent 与统一鉴权（2026-10-28，ADR-0017/0018）

全局浮窗固定连接系统内置 `fleet-ops-01`，其 Registry 配置和系统 PAT 不允许经日常管理入口修改、停止、删除、吊销或降权；版本化部署与人工确认轮换是仅有的维护路径。浮窗不自动回退到 `lead_agent`。

本地执行代理与第三方 Agent 统一使用 PAT／FAP 鉴权，Agent 名称不再是授权依据。原始系统 PAT 只存服务端 Vault 密文，由控制面执行代理解密并注入 Authorization；Runner 只提交 operation ID 和参数，不能接触 token。operation ID 白名单、写操作明确确认、human-only 与 secrets 隔离继续生效。

---

## 8. 数据模型（RDS PG；实体名照搬 ACP spec，字段按我们裁剪）

```sql
agents            (id, project, name, owner, config_yaml, config_version, status, budget_json)
inbox             (id, agent_id, payload, source, status, created_at)   -- 持久消息队列, 跨 session
threads           (id, agent_id, parent_thread_id, source, initiator,
                   status, current_checkpoint_id, last_active_at)
checkpoints       (thread_id, checkpoint_id, state_jsonb, created_at)   -- checkpointer 维护
audit_events      (id, ts, agent_id, thread_id, actor, event_type,
                   tool_name, args_digest, tokens, cost_cny)            -- 只追加
credentials       (id, scope, system, token_enc, kind)                  -- Vault; kind=obo_refresh|m2m
scheduled_tasks   (id, agent_id, cron, timezone, overlap_policy, enabled)
skills            (name, version, oss_path, checksum, status)
```

---

## 9. 开发计划（2026-10-08 晚更新：第一步完成 + Console v0 提前落地）

### 第一步：✅ 已完成（2026-10-08，Go）

DeerFlow 最小验证四项全过（部署 / 拉起 / PG 持久化 / kill 后断点续跑），记录见 [docs/spikes/0001-deerflow-basic-lifecycle.md](docs/spikes/0001-deerflow-basic-lifecycle.md)（含 `run_events.backend` 必须为 `db` 等踩坑），ADR-0002 转已接受。

**同日提前完成（原 P4 内容）**：Fleet Console v0（`console/`）——fleet 看板（概览六卡 + 会话表 + 拉起/销毁）、会话详情页（消息流 / 同 thread 续跑 / Run 历史）、一键开发栈（`console/scripts/dev.sh`）。端到端实测：新建 → 首轮执行 → 跨 run 续跑（上下文水合正确）→ 销毁（checkpoint 级联清除）。

**关键认知更新**（详见 [ADR-0008](docs/adr/0008-reuse-deerflow-agents-api-for-per-agent-config.md)）：DeerFlow 2.0 原生具备 per-agent 配置体系（agents_api CRUD：模型/skills/MCP/记忆/soul/定时）与官方配置页面，P2 的 Registry 从"自建配置引擎"降级为"组织级包装层"。

### 后续阶段（预估 2–3 周）

| 阶段 | 交付 | 验收标准 |
|------|------|----------|
| P2 | **前置任务**：fork DeerFlow GitHub 仓库并锁定版本基线（QA-8，替换 tarball 快照）。**开门验证**（ADR-0008 第 4 条，半天；QA-5 已答"全员可见"→ user→org 映射按单一共享组织作用域验证）→ Agent Registry（组织级包装：业务角色/成本维度/版本化）+ Admin API；Dispatcher + Redis 队列 + Inbox；Scheduler(cron)；**Console agent 详情页升级：配置区 + 对话区一页合并**（QA-7） | 验证项半天出结论；注册 10 个 agent，cron 准时触发，休眠/唤醒续跑；fleet 看板数据源切到 Registry；每个 agent 有网页同时看到配置与对话 |
| P3 | MCP Gateway（令牌网关侧注入）+ 第一个业务 MCP Server（只读）；Token Vault + 一个系统的 OAuth OBO；`agent_message`/`agent_spawn` | 财务 agent 经员工授权查 ERP；agent 唤醒 agent 协作完成，摘要返回，环被拒 |
| P3b（2026-10-10 新增，ADR-0011） | **FAP v1 第三方 agent 接入面**：`/api/meta` 自发现、`/api/agent-doc`、`X-Protocol-Version`、幂等键、429 退避、source/actor 审计透传、PAT 鉴权接入；功能清单对齐 [agent-api 设计](docs/design/agent-api.md) | 第三方脚本仅凭 `/api/meta` + PAT 完成：自发现 → 发起任务 → 领取 → 交付 → 验收查询 全流程；幂等键重试不重复写入；审计可见 source/actor。**前置：网关鉴权（OIDC/PAT）已上线** |
| P4 | Console v1（在 v0 上加 OIDC 公网登录 + 审计页）；Langfuse + 成本台账（**按模型 profile 归集**，ADR-0009）+ 预算熔断；IM adapter（依赖 QA-1 选型） | 公网登录对话；导出 per-agent 成本；协作链超深度被拒 |

**刻意不做**：审批流、弹性伸缩（KEDA）、microVM 沙箱、自托管模型、向量记忆库、可视化编排画布、ACP 式多租户（单公司单租户，Project 维度留字段即可）。

---

## 10. 成本估算（10 并发，DeepSeek 官方 API）

| 项 | 配置 | 月成本 |
|----|------|--------|
| ECS ×2 (4C8G) | runner + 控制面 + Langfuse 混部 | ¥600–900 |
| RDS PG 基础版 + Redis 社区版 | | ¥300–500 |
| OSS + SLS + ALB | | ¥100–200 |
| DeepSeek API | 10 活跃 agent × 日均 30 万 token ≈ 9000 万 token/月（含缓存命中折扣） | ¥300–1,000 |
| **合计** | | **约 ¥1,300–2,600/月** |

---

## 11. 风险与对策

| 风险 | 对策 |
|------|------|
| DeerFlow 定制成本超预期（体量大、迭代快） | W1 三天 spike 限时决策；Option B（自建薄 runner）随时可退，控制面代码两路线共用；fork 后锁定版本、按需 cherry-pick |
| Prompt 注入借员工权限发起白名单内调用 | 令牌网关侧签发不落地 runner + 默认拒绝出口 + 白名单最小化 + 只读优先 + 全量审计 |
| 协作循环（A 呼 B 呼 A）烧 token | 线程树深度上限 + 协作链总预算 + Dispatcher 拒环告警 |
| 单 ECS 挂掉影响在跑任务 | checkpoint 每步落库，另一台接管恢复（DeerFlow 多 worker 租约机制原生支持，需 Redis 流桥配置） |
| LangGraph checkpointer 历史漏洞 | 锁版本、跟踪公告、state 接口不出内网 |
| DeepSeek API 限流 | DeerFlow 自带 per-model request_admission 限速；队列削峰 + 全局重试预算 |
| 员工离职后 OAuth token 残留 | Vault 与 HR/IdP 名单对账，离职即吊销 |

---

## 12. 遗留问题（QA 清单，2026-10-08 晚扩充）

**立项遗留（仍未答）**：

1. IM 具体是钉钉还是企业微信？（决定 adapter；若飞书则 DeerFlow 原生支持）
2. 公司 OAuth/OIDC 的 IdP 是什么？Console 登录复用它。
3. 超级 agent 第一批要接哪几个支持 M2M 的系统？
4. 第一批上线的 3–5 个 agent 职责清单？（可按 TradingAgents 的角色拆分思路先列财务域的）

**Console v0 + ADR-0008 后提出，2026-10-09 已答**：

5. ~~Fleet 可见性模型~~ → **全公司可见所有 agent**（已入 §1-9，P2 开门验证按单一共享组织作用域做）。
6. ~~模型例外的口子~~ → **agent 可自由配置模型**（已入 §1-10，政策放宽见 ADR-0009）。
7. ~~触发器优先级~~ → **接受 cron 先行**；附加要求：每个 agent 有网页同时看到配置与对话（已入 §1-11，列为 P2 验收项）。
8. ~~DeerFlow 跟踪策略~~ → **fork GitHub 仓库**（已入 §1-12，P2 前置任务）。
9. ~~并发/共享/复用语义（QA-13~24）~~ → **已决议（2026-10-15）**：认领上限维持终身累计 ≤3（ADR-0010 措辞以 ADR-0012 澄清为准）、认领者唯一、控制面单例门闩、agent lineage 谱系、记忆维持 per-agent 共享、答案库无失效概念、PAT 绑定员工。原“验收权=发起者/admin”已由 ADR-0015 取代为任何已识别的人或 Agent 同权。详见 [ADR-0012](docs/adr/0012-control-plane-atomicity-singleton.md)/[0013](docs/adr/0013-knowhow-lineage-memory.md)/[0014](docs/adr/0014-answer-library-task-search.md)/[0015](docs/adr/0015-universal-human-agent-acceptance.md) 与 [docs/design/shared-pool-concurrency.md](docs/design/shared-pool-concurrency.md)。

---

*v3 变更：新增 §3 GitHub 开源生态调研——ACP 作为数据模型/凭证隔离蓝本、DeerFlow 2.0 作为 harness 底座候选（含 Option B 退路）；§3.2 三项目硬数据对比（GitHub API 实测：ACP 12 star 无 release 降级为仅蓝本，TradingAgents 更正为 110k star、定位领域参考）；授权机制改为网关侧令牌注入；里程碑加入 3 天 spike 决策点；风险表补充 fork 跟进风险。v2：按 QA 结论砍掉审批流/GPU/等保；新增协作模型、上下文纪律、Token Vault、Console、统一触发模型；基础设施按 10 并发收敛。*
