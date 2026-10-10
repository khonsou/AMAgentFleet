# Agent Fleet 概念模型与 GUI 术语梳理

> 2026-10-10。目的：回答「系统到底分几层、每层列表/详情对应什么概念」，
> 并给出现状 GUI 中所有术语混乱点的清单与统一命名建议。
> **本文档不含代码改动**；第 5 节的命名映射是后续改文案的唯一依据。

## 1. 分层模型（模型层真相，以数据表为准）

系统共 **6 层实体**，自上而下：

| 层 | 实体 | 存储 | 定义 | 生命周期 |
|----|------|------|------|----------|
| L0 | **Agent 定义** | `fleet_agents` + `agents/*.yaml` + 网关 custom-agent 配置 | 一个 agent 的配置面：soul/模型/工具/预算/触发器/egress。runner 无状态，配置即一切 | registered →（stop）→ hibernated →（start）→ registered；destroy → terminated（配置删除，不可 start 回来） |
| L1 | **Task 任务** | `fleet_tasks` | **协作与验收单元**。一条自包含指令；谁创建谁验收；可挂树（`parent_task_id`，A2A 拆出来的子任务） | pool_status：open（待领取）→ review（有交付待确认）→ done（已验收/已关闭）；status：queued/success/failed |
| L2 | **Loop 执行轮次** | `fleet_task_loops` | **一次领取 = 一次执行尝试**。同一 task 可竞争多个 loop（agent ≤3，human 不限）；打回只打 loop 不打 task，同 thread 续跑 | claimed → running → delivered ⇄ returned → accepted/archived |
| L3 | **Thread 会话** | `threads_meta`（DeerFlow） | **agent 的一次对话容器**。agent 的 loop 起跑时绑定，1 个 agent-loop 对应 1 条 thread；打回续跑复用同 thread；human loop 无 thread | 随 agent 运行 idle/running；checkpoint 续水合 |
| L4 | **Run 运行** | `runs` | **一次图执行**（LangGraph run），token/成本记账与生命周期（success/error）的单位；thread 内可有多个 run（每次续跑 = 新 run） | created → running → success/error |
| L5 | **Message 消息** | `messages` | 对话与工具调用事件流（user/ai/tool），展示与审计的最细粒度 | 只增不改 |

横切设施（不属于上面任何一层）：

- **Inbox**（`fleet_inbox`）：挂在 agent 上的持久消息队列，攒批合并为 task 唤醒 agent（休眠 agent 拒收）。
- **Cron**（`fleet_cron_fires` + YAML `triggers.cron`）：定时触发器，到点投递为开放 task 由舰队指挥官认领。
- **PAT / Settings**：第三方接入令牌，平台配置面。

### 1.1 实体关系（基数与关键字段语义）

```
Agent 1 ────< Task（认领；Task 也可 human/unassigned 认领）
Task  1 ────< Loop（竞争，非独占）
Loop  1 ───── 1 Thread（仅 agent loop；打回续跑同 thread 复用）
Thread 1 ────< Run
Run   1 ────< Message
Task  1 ────< Task（parent_task_id 树，深度 ≤3，A2A 拆任务）
Agent 1 ────< Inbox 消息；Agent 1 ────< Cron 触发器
```

**⚠️ 最容易误解的字段**：`fleet_tasks.agent_name` 存的**不是「拥有者」而是
「认领人」**（unassigned / human / <agent-name>）。它表达的是 task 当前被谁
领取，不是 agent 的资产清单字段。Agent 详情页「该 agent 的任务列表」实际是
按这个字段反查的「认领记录」。

**Loop 与 Thread 的绑定口径**：dispatcher 起跑前预生成 thread_id 落库
（`fleet_task_loops.thread_id`），所以是严格的 1:1——loop 排队中没有
thread_id，起跑后才有。看板上「排队中的 loop 无 thread 链接」即此。

## 2. 页面 × 概念矩阵（现状 catalog）

| 页面 | 路由 | 承载层级 | 列表实体 | 详情实体 | 概念准确度 |
|------|------|----------|----------|----------|-----------|
| 任务看板（Fleet Board） | `/` | L1 为主，L2 验收台 | **task**（含子任务树） | task（卡片链接 → /tasks/:id） | ⚠️ 见 §3-T1~T4 |
| Agent 详情 | `/agents/:name` | L0 配置 + L1 认领记录 | 右栏列的是**该 agent 认领的 task** | task（→ /tasks/:id） | ⚠️ 见 §3-T5 |
| Task 详情 | `/tasks/:id` | L1 + L2 | task 的 **loops** | loop（含 thread 链接） | ✅ 全站最准确 |
| Thread 详情 | `/threads/:id` | L3 + L4 + L5 | runs | messages | ✅ 准确 |
| 运行观测 | `/threads` | L3 | thread 日志 | thread | ✅ 准确（备用查询定位） |
| Agent 定义（Registry） | `/agents` | L0 | agent 定义 | agent 定义 | ✅ 准确（配置面） |
| 设置 | `/settings` | 平台 | PAT | — | ✅ 准确 |

典型浏览路径的概念流（以今天验收过的真实链路为例）：

```
任务看板(任务卡片) → Task 详情(任务输入 + loops) → Thread 详情(runs/messages)
     ↓ 点击认领人                    ↑ loop 的 thread 链接
Agent 详情(配置 + 该 agent 认领的 task 列表) ───────────→ Task 详情
```

每一步跳转层级是连贯的（L1→L1→L2→L3→L4/L5）；混乱不在信息架构，
**在文案命名**（§3）。

## 3. 术语混乱点清单（现状 → 为什么是错的）

| # | 位置 | 现状文案 | 实际语义 | 问题 |
|---|------|----------|----------|------|
| T1 | 看板发起框 | `NEW LOOP / 发起` | 创建的是 **task**（入池待认领，或指派建 loop 的是 dispatcher 的事） | 用户「发起一个 loop」——loop 是领取时才产生的，发起阶段根本不存在 loop |
| T2 | 看板卡片头 | `loop #<id>` | 这是 **task** 的 id | 直接张冠李戴；同一张卡片正文里又写 `task #<id> 详情 →`，一张卡片两个名字 |
| T3 | 看板统计 | `RUNNING LOOPS` | 执行中的是 **task**（tagOf 按 pool_status/认领人判定，与 loop 无关） | 计数对象与名字不符 |
| T4 | 看板流区标题 | `TASK STREAM` | 内容是 task 树 | 英文名尚可，但与 T1 的 LOOP 并存造成「这站到底叫 task 还是 loop」的困惑 |
| T5 | Agent 详情右栏 | 面板名 `SESSION / 对话（经 Dispatcher 派发）`，计数 `N TASKS`，行内链接 `task #N 详情 →` | 列表实体是 **task**（认领记录），不是会话 | 面板标题说「对话/会话」，内容是任务卡；用户预期看到聊天历史，实际看到任务流 |
| T6 | 同页底部输入框 | 占位「输入指令派发新任务（新 thread 执行）」 | dispatch = 建 task 并指派 | 「新 thread 执行」把 L3 提前暴露给用户；用户不需要知道 thread 在此时创建 |
| T7 | 全站同义词 | 发起/派发/dispatch 三个词混用；会话/session/对话/thread 混用；任务/task/loop 混用 | — | 无统一词表，文案各处即兴 |
| T8 | Task 详情页 | ✅ `INPUT / 任务输入`、`LOOPS / 领取与执行记录` | 准确 | 无问题，作为正例保留 |

补充说明两处「看似混乱实则正确」的：

- **Task 详情里出现 loops**：正确。task 是验收单元，loops 是它的执行记录，
  这正是 L1/L2 关系的正常呈现。
- **Thread 详情里出现 messages/runs**：正确。L3 容器展开就是 L4/L5。

## 4. 词表（唯一标准，后续文案以此为准）

| 标准词 | 英文 | 定义（一句话） | 禁止混用 |
|--------|------|----------------|----------|
| **任务** | task | 协作与验收单元，一条自包含指令 | 不叫 loop / 单 / 需求 |
| **执行轮次** | loop | 一次领取的一次执行尝试（口语可叫「一次执行」） | 不叫任务/重试（returned 也是同 loop） |
| **会话** | thread | agent 的一次对话容器（绑定一个 agent loop） | 不叫 session / 对话记录 |
| **运行** | run | 一次图执行，token 记账单位 | 不直接对用户露出（除观测页） |
| **消息** | message | 对话与工具调用事件 | — |
| **认领** | claim | task 被领取（人/agent 同词） | 不叫接单/分配（指派是 assign） |
| **指派** | assign | 人把 task 指定给某 agent | 不等于认领 |
| **交付** | deliver | loop 产出交付物，task 入 review | — |
| **验收** | accept/return | 采纳（task→done）/ 打回（loop 续跑） | 不叫通过/驳回 |
| **发起** | create | 人创建 task 入池 | 不叫派发（dispatch 仅指「发起并指派给某 agent」） |

## 5. 命名统一建议（改动映射，纯文案，待批准后执行）

| 位置 | 现状 | 建议 | 涉及面 |
|------|------|------|--------|
| 看板发起框 | `NEW LOOP / 发起` | **`NEW TASK / 发起任务`** | Home.tsx 文案 |
| 看板卡片头 | `loop #<task.id>` | **`task #<task.id>`**（与行内链接一致） | Home.tsx 文案 |
| 看板统计 | `RUNNING LOOPS` | **`执行中任务`（RUNNING TASKS）** | Home.tsx 文案 |
| 看板流区 | `TASK STREAM` | **`任务流 / TASK STREAM`**（保留，中文标注） | Home.tsx 文案 |
| Agent 详情右栏 | `SESSION / 对话（经 Dispatcher 派发）` | **`TASKS / 认领任务`**（计数已是 TASKS） | AgentDetail.tsx 文案 |
| 同页输入框占位 | 「…（新 thread 执行）」 | 去掉 thread 暴露：「输入指令发起新任务，Ctrl/⌘+Enter 指派」 | AgentDetail.tsx 文案 |
| Registry 页头 | 「注册 / 配置 / 停止拉起。派发与协作运行流见 Fleet 看板」 | 与词表对齐：「发起见任务看板」 | Registry.tsx 文案 |
| 运行观测页 | `THREAD LOG / 执行日志` | **`会话日志 / THREAD LOG`** | Threads.tsx 文案 |

执行原则：

1. **只改文案不改信息架构**——层级与跳转（§2 矩阵）已验证连贯。
2. 所有改动引用 §4 词表，禁止引入新同义词。
3. 改完以 §2 矩阵为 checklist 逐页核对（每个页面只允许出现其承载层的标准词）。
4. `api.*` 函数名、`fleet_*` 字段名不动（实现层命名与展示层命名解耦，
   实现层保持现状即可，ADR-0008 已定配置层语义）。

## 6. 决策记录位

- **2026-10-10 批准并执行**：本文档 review 通过，§5 映射表 8 处文案全部落地
  （commit 见 git log）；执行时额外统一一处：验收台退回意见占位「同 thread
  续跑」→「在同一会话中续跑修正」。保留不动的正确用法：TaskDetail/验收台中
  「采纳/退回/重派 loop #N」（动作对象就是执行轮次，属准确术语）。
  核对结论：tsc ✓、Vite 编译 ✓、逐页 silk/placeholder 术语复核 ✓。
- **2026-10-10 消灭 UNIT**：详情页眉标 `UNIT / X DETAIL` 经追问确认不是任何
  概念，只是与列表页 `MODULE NN` 对应的装饰性排版词（详情页版），从未进入
  概念模型。替换为所属模块归属（复用既有 MODULE 约定、零新概念）：
  TaskDetail → `MODULE 01 / FLEET BOARD · TASK`；AgentDetail →
  `MODULE 02 / REGISTRY · AGENT`；ThreadDetail → `MODULE 03 / THREADS · THREAD`。
  Registry 计数 `N UNITS` → `N AGENTS`。全站 UNIT 清零（tsc ✓）。
  **教训**：眉标/装饰词也是用户会读到的词，新词进入 GUI 前须过 §4 词表。
- **2026-10-10 MODULE 与概念层级解耦 + ThreadDetail 层级眉标**：确认
  `MODULE NN` 编号只是顶栏导航顺序位的视觉装饰（机架设计语言），与概念
  模型 L0–L5 无任何对应关系，保留。同时 Thread Detail 页头新增概念层级眉标
  `TASK #N › LOOP #M › THREAD · 任务内容`（TASK 可点击回 Task 详情；任务
  内容为主、loop 编号低调跟随，沿用 loop 卡片约定）。无任务上下文的自由
  对话 thread 退化为纯 `MODULE 03 / THREADS`（`loop: null` 是有真实语义的
  fallback，非错误）。层级词全部来自 §4 词表，零新概念。实现：by-thread
  接口补带 task.input；验收条 ReviewBar 与层级眉标共用同一份 reviewContext
  （页面级统一拉取）。
- **2026-10-10 详情页层级眉标补全（三页对齐）**：层级链上每个有父级的详情页
  都应有 breadcrumb——TaskDetail 新增 `TASK #N`（有父任务时
  `TASK #父 › TASK #N`，父任务可点击；原 meta 行里的「父任务 #N」迁入
  breadcrumb 不再重复显示）；AgentDetail 本身处于链顶（agent 定义无父实体），
  无 breadcrumb，仅将眉标统一为纯 `MODULE 02 / REGISTRY`（实体身份由 h1 承担，
  与 Thread/Task 页新模式一致）。向下链接（loop→thread、task→loop）此前已齐。
