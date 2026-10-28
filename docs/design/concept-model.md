# Agent Fleet 概念模型与 GUI 术语梳理

> 2026-10-10。目的：回答「系统到底分几层、每层列表/详情对应什么概念」，
> 并给出现状 GUI 中所有术语混乱点的清单与统一命名建议。
> **本文档不含代码改动**；第 5 节的命名映射是后续改文案的唯一依据。

## 1. 分层模型（模型层真相，以数据表为准）

系统共 **6 层实体**，自上而下：

| 层 | 实体 | 存储 | 定义 | 生命周期 |
|----|------|------|------|----------|
| L0 | **Agent 定义** | `fleet_agents` + `agents/*.yaml` + 网关 custom-agent 配置 | 一个 agent 的配置面：soul/模型/工具/预算/触发器/egress。runner 无状态，配置即一切 | registered →（stop）→ hibernated →（start）→ registered；destroy → terminated（配置删除，不可 start 回来） |
| L1 | **Task 任务** | `fleet_tasks` | **协作与验收单元**。一条自包含指令；任何已识别的人或 Agent 均可验收（ADR-0015）；可挂树（`parent_task_id`，A2A 拆出来的子任务） | pool_status：open（待领取）→ review（有交付待确认）→ done（已验收/已关闭）；status：queued/success/failed |
| L2 | **Loop 执行轮次** | `fleet_task_loops` | **一次领取 = 一次执行尝试**。同一 task 可竞争多个 loop（agent ≤3，human 不限）；打回只打 loop 不打 task，同 thread 续跑 | claimed → running → delivered ⇄ returned → accepted/archived |
| L3 | **Thread 会话** | `threads_meta`（DeerFlow） | **agent 的一次对话容器**。agent 的 loop 起跑时绑定，1 个 agent-loop 对应 1 条 thread；打回续跑复用同 thread；human loop 无 thread | 随 agent 运行 idle/running；checkpoint 续水合 |
| L4 | **Run 运行** | `runs` | **一次图执行**（LangGraph run），token/成本记账与生命周期（success/error）的单位；thread 内可有多个 run（每次续跑 = 新 run） | created → running → success/error |
| L5 | **Message 消息** | `messages` | 对话与工具调用事件流（user/ai/tool），展示与审计的最细粒度 | 只增不改 |

横切设施（不属于上面任何一层）：

- **Inbox**（`fleet_inbox`）：挂在 agent 上的持久消息队列，攒批合并为 task 唤醒 agent（休眠 agent 拒收）。
- **Cron**（`fleet_cron_fires` + YAML `triggers.cron`）：定时触发器，到点创建 task/loop，默认由 YAML 所属 Agent 认领；仅显式 `orchestrator` 或全局 `FLEET_COMMANDER_AGENT` 指定编排者（ADR-0016）。
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

## 2. 页面 × 概念矩阵与导航规则

| 页面 | 路由 | 承载层级 | 列表实体 | 详情实体 | 导航定位 |
|------|------|----------|----------|----------|----------|
| 任务实例看板（Fleet Board） | `/` | L1 为主，L2/L3 为执行主体；L1 内交付验收 | **task instance**（含子任务树） | task（卡片链接 → /tasks/:id） | 实例链入口；Output 优先，Agent 定义为辅助引用 |
| Agent 详情 | `/agents/:name` | L0 定义 + L1 关联实例 | 右栏列的是**该 agent 关联的 task instance** | task（→ /tasks/:id） | 定义页；关联实例是横向引用 |
| Task 详情 | `/tasks/:id` | L1 + L2 | task 的 **loop instances** | loop（含 thread 链接） | 实例链主枢纽 |
| Thread 详情 | `/threads/:id` | L3 + L4 + L5 | runs | messages | 实例链末端 |
| 运行观测 | `/threads` | L3 | thread instance 日志 | thread | 独立实例查询入口 |
| Agent 定义（Registry） | `/registry` | L0 | agent definition | agent definition | 定义链入口 |
| 设置 | `/settings` | 平台 | PAT | — | 平台配置面 |

**定义线与实例线必须分开：**

```
Agent Registry → Agent Definition ──横向筛选──→ Task Instance
Fleet Board → Task Instance → Loop Instance → Thread Instance → Run / Message
```

- `Task → Loop → Thread` 是实例层级链；Agent Definition 不进入这条 breadcrumb。
- `Agent Definition ↔ Task/Thread Instance` 是横向引用，链接文案必须明确「Agent 定义」或「关联任务实例」。
- Agent Definition 的关联任务卡只进入 Task Detail，不从 task 越层直达 thread。
- Loop 没有独立页面，继续寄居 Task Detail；全局 Threads 页保留为排查入口。

因此，定义与实例混排以及 Task 卡片直达 Thread 属于**信息架构问题**，不是单纯文案问题。

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

> 提炼版速查与 PR 检查单见 [gui-copy-spec.md](gui-copy-spec.md)，本词表是唯一权威。

| 标准词 | 英文 | 定义（一句话） | 禁止混用 |
|--------|------|----------------|----------|
| **Agent 定义** | agent definition | YAML、配置版本与生命周期组成的声明式定义 | 不叫 agent 实例 / runner |
| **任务实例** | task instance | 协作与验收单元，一条自包含指令；首页创建的也是实例 | 不叫 task definition / loop / 单 / 需求 |
| **执行轮次实例** | loop instance | 一次领取的一次执行尝试（口语可叫「一次执行」） | 不叫任务/重试（returned 也是同 loop） |
| **会话实例** | thread instance | agent 的一次工作会话（绑定一个 agent loop） | 不叫 session / 对话记录 |
| **运行** | run | 一次图执行，token 记账单位 | 不直接对用户露出（除观测页） |
| **消息** | message | 对话与工具调用事件 | — |
| **认领** | claim | task 被领取（人/agent 同词） | 不叫接单/分配（指派是 assign） |
| **指派** | assign | 人把 task 指定给某 agent | 不等于认领 |
| **交付** | deliver | loop 产出交付物，task 入 review | — |
| **验收** | accept/return | 采纳（task→done）/ 打回（loop 续跑） | 不叫通过/驳回 |
| **发起** | create | 人创建 task instance 入池 | 不叫派发（dispatch 仅指「发起并指派给某 agent」） |
| **任务池** | task pool | 全部任务实例的协作池（ADR-0010） | 不叫任务流 |

### 4.1 页面层级眉标规范（breadcrumb 约定）

详情页页头的固定版式，自上而下四行：**返回链接 → MODULE 眉标（视觉装饰，
纯模块名，不带实体后缀）→ 概念层级 breadcrumb（仅当有父级时）→ 实体标题 h1**。

- **实例 breadcrumb**：只允许 `TASK #N › LOOP #M › THREAD`；Agent Definition
  是横向引用，不得插入该层级。
- **每段可点击性**：TASK 链接到 Task Detail；LOOP 链接到寄居页锚点
  `/tasks/:id#loop-N`（滚动定位 + 短暂高亮）；当前页 THREAD 为纯文本。
- **链顶页无 breadcrumb**：Agent Definition 上方只有 Registry 模块，不硬造
  breadcrumb；其实例列表必须标为「关联任务实例」。
- **fallback 语义**：自由 thread 无 loop 时实例 breadcrumb 整行消失，
  `loop: null` 是真实语义而非错误——禁止显示空壳或报错。
- **MODULE 眉标**：编号只是顶栏顺序位（01 FLEET / 02 REGISTRY / 03 THREADS /
  04 SETTINGS），与概念层级无关。
- **禁例**：不得在页头引入无概念对应的装饰词（UNIT 事件教训，见 §6）。

## 5. 定义 / 实例分离改造映射

| 位置 | 改造 | 涉及面 |
|------|------|--------|
| 首页 | 标题与发起框明确为任务实例；删除 task 卡片 thread 直达链接 | Home.tsx |
| Agent 详情 | 返回 Registry；左侧标 Agent Definition，右侧标关联任务实例；删除任务卡 thread 链接 | AgentDetail.tsx |
| Task 详情 | Agent 链接标为定义引用；保留 loop → thread | TaskDetail.tsx |
| Thread 详情 | 保留 TASK › LOOP › THREAD；Agent 定义作为独立横向链接 | ThreadDetail.tsx |
| Threads 列表 | 标为 Thread 实例观测；Agent 链接标为定义引用 | Threads.tsx |
| 路由与 API | 全部保持兼容，不改数据库和状态机 | 无 |

执行原则：

1. 实例层级只有 `Task → Loop → Thread → Run/Message`；跨定义链接必须显式标注。
2. 所有改动引用 §4 词表，禁止引入新同义词。
3. 改完以 §2 矩阵为 checklist 逐页核对。
4. `api.*` 函数名、`fleet_*` 字段名不动；`task.thread_id` 仅为兼容保留，不再作为前端导航依据。

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
- **2026-10-10 编号/用词统一修正（13 处，纯文案+锚点）**：
  A. 裸编号归位：task 卡片头 `#156 · web`（紧邻 agent 名易被误读为 agent
     编号）→ `task #156 · web`；
  B. 「对话」→ 标准词「会话」×4（验收台链接、Thread 验收条、运行观测说明、
     MESSAGES 副标）；
  C. thread 链接文案三种写法统一为 `会话 {8位id} →`（看板卡片、Agent 详情
     token 行、Task 详情 loop 卡）；
  D. LOOP breadcrumb 段由纯文本升级为**寄居页锚点链接**
     （`/tasks/:id#loop-N`，落点滚动定位 + 琥珀高亮 2s），§4.1 可点击性
     规则同步修订（「寄居页段为纯文本」→「链接到寄居页锚点」）；
  E. 「任务流 / TASK STREAM」统一回「任务池 / TASK POOL」——按 ADR-0010，
     pool_status 是每个 task 的生命周期字段，所有任务皆在池中，「任务流」
     只是池的可视化说法；**此条反转 §5 映射表「任务流保留」的旧决策**。
     §4 词表新增「任务池」词条（不叫任务流）。
- **2026-10-10 loop OUTPUT 展示派生（方案 A）**：「OUTPUT / 交付内容」主显
  thread 最新 AI 回复（派生自 run_events），物理快照 `deliverable` 降级为
  「交付快照 vN（入库时的交付记录）」低调标注，仅在两者不一致时出现。
  背景 bug：人直接进 Thread Detail 续对话不经过 dispatcher，`deliverable`
  快照停在旧回复，而快照本应是「当前 output」。方案 A 的理由：loop 与
  thread 1:1，thread 最新 AI 回复即该 loop 的当前 output，展示无需物理落库
  第二份；退回重派路径本来就重新捕获快照，语义不受影响。实现：控制面
  task detail 附 `latest_output`（单条 SQL 按 thread_id 批量取）；前端共享
  `LoopOutput` 组件用于 TaskDetail loop 卡与验收台；对比时剥掉 dispatcher
  追加的「📎 产物：」段。零新概念、零信息架构改动。
- **2026-10-27 定义 / 实例信息架构分离**：确认首页创建的是 task instance，
  Agent Detail 是 definition，Thread 是 agent loop 的工作实例。反转此前
  「混乱不在信息架构，只在文案」及「只改文案不改信息架构」的旧结论：
  删除 Home/Agent Detail 基于 `task.thread_id` 的越层会话入口，实例统一走
  Task → Loop → Thread；Agent Definition 与实例之间只做显式横向引用。
  Agent Detail 返回 Registry，右侧任务列表标为关联实例。路由、API、数据库、
  状态机均保持兼容；本决策不改变 ADR-0010 的 task/loop 运行语义。
- **2026-10-27 实例执行层与交付验收**：首页以 Task 与最终 Output 为主，
  每个 task 的最新 Loop/Thread 在执行区主体展示，Agent Definition 降为底部
  横向辅助引用；不再使用 `task.thread_id` 越层导航。「验收台」统一改为 task 内
  的「交付验收」，不引入独立验收实例。采纳/退回对任何已识别的人、内部 Agent、
  FAP/A2A + PAT 第三方 Agent 同权，关闭作废不在放开范围；PAT 身份覆盖 body
  自报 author 进入审计，见 ADR-0015。
