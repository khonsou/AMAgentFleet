# Agent Fleet 表现层调研与双轨原型评估

日期：2026-10-30
范围：GitHub 与 X 上的 Agent 集群表现层，以及同一份 dummy data 的纯 H5 原型
约束：不修改现有 Console 的功能或代码；原型只用于观察、比较与取舍

## 1. 最新方向：A1 负责工作区，B 独立为纯游戏状态窗口

**“现有页面是否更好用”与“Fleet 协作是否更直观、更有趣”仍是两个独立目标。** 最新原型只保留 A1 与 B 两个入口，并彻底分开信息展示与游戏体验的职责。

### A 线：现有页面的信息效率优化

目标是减少阅读与操作成本，继续回答“哪个 Task 需要我、谁在做、进展到哪里、OUTPUT 是什么”。

- **A1 紧凑列表保持现状**：保留列表心智，减少字段、折叠详情、突出注意点。
- **A1 独占全部详情与操作**：Task 层级、LOOP、Thread、Tool JSON、对话、OUTPUT、审批、退回与重试都在 A1 工作区完成。
- **A2 Task 执行地图已删除**：不再与 A1 分散同一轮信息效率评估。
- **A3 状态看板已删除**：不再把父子 Task 拆到不同状态列中比较。

### B 线：纯游戏 Fleet 指挥室

旧 B1 Fleet 拓扑、B2 Agent 时间线、B3 Task 流动视图已全部删除。B 不再寻找另一种图，也不再兼任 A1 的对话工作区，而是采用以下心智：

- **Agent 是队伍中的角色，游戏场景只表达 Fleet 的在场感与工作状态。**
- 首屏只保留角色、工位、状态动效、WORKING／OUTPUT／ALERT 三项状态计数与视角控制。
- 办公室改为没有边界的连续草地，角色与工位的世界尺寸保持不变；相机不再受固定地图限制，可以持续平移和扩展布局。默认相机 zoom out，并支持背景拖拽、滚轮缩放、＋／−与复位视角。
- 点击角色只在游戏内打开摘要浮窗，展示 Agent、Task、LOOP／Thread 与当前 OUTPUT；不切换 hash、不退出全屏。
- B 不展示完整 Thread、Tool JSON、对话输入或审批操作；需要完整信息和操作时返回 A1。
- 支持浏览器原生全屏与 LIVE／PAUSED。

角色位置与动效是视觉隐喻，不代表真实物理协作，也不虚构完成百分比。技术归属仍遵守真实的 Task → LOOP → Thread 关系，完整技术详情与操作只由 A1 承担。

### 引擎与视觉参考

B 使用 **Phaser 4**，而不是只负责渲染的 PixiJS。Phaser 的 Scene、输入、相机、Tween、粒子、物理与音频体系能支撑后续角色移动、房间切换、事件编排和更完整的游戏交互；PixiJS 更适合作为渲染层，本次不为它另行补齐游戏框架。

视觉已放弃初版深色像素办公室，也不再保留悬浮岛方案；从用户提供的 **Let’s Make Rainbow** 参考中仅保留圆润黏土角色、低饱和配色、柔光阴影与轻微弹性动效。[参考游戏](https://g-fc45961d-08e3-49b3-80c4-0a386dbca870.ohmygame.zone/)

当前场景全部由 Phaser 绘制：连续草地上只有 6 位角色、独立工位与状态徽记；环形跑道、盆栽、彩虹和装饰性浮动光点均已移除。背景层加入 4 朵半透明云与 2 组鸟群，以不同速度缓慢飞过，鸟群带有轻微振翅与起伏；它们始终位于角色后方，并在飞离视野后循环。WORKING 角色有呼吸与眨眼，OUTPUT 与 ALERT 使用不同的漂浮徽记和脉冲反馈，角色之间仅保留表达工作传递的柔和光点。草地纹理跟随相机可视范围无缝铺设，渲染使用 Canvas 与 2 倍纹理；角色名牌与状态徽记保持上层可读。DOM 只承担窗口控制、视角控制与按需摘要浮窗。

### 当前状态

A1 与新 B 已用同一份 dummy data 实现。新 B 支持 6 个角色的状态展示、无限草地平移缩放、云与鸟群背景动画、LIVE／PAUSED、全屏、桌面／移动布局，以及不离开游戏的角色摘要浮窗。A2、A3 与旧 B 的 hash 路由统一回到 A1。

本轮验证覆盖单画布、6 个角色、云与鸟群的首屏可见性及漂移动画、场景暂停／恢复、相机拖拽／缩放／复位、超远距离平移后的草地完整覆盖、全屏内浮窗、桌面与移动端浮窗，以及 B 中不存在对话和审批面板；另对桌面、全屏和手机截图做了视觉复核。真实 Console 尚未改动，是否接入仍等待体验后的决策。

## 2. 调研方法与证据边界

本轮进行了 11 轮有新增信息的检索，并复核了重点项目的 GitHub README 或原始文档。比较重点是信息架构与交互方式，不把 star 数、视觉风格或项目宣传当作成熟度的唯一依据。

证据边界：

- 下文项目能力以其公开 README／文档为依据，未逐一部署做独立验收。
- 部分项目仍处于 alpha、pre-1.0 或实验阶段；这里借鉴的是表现层模式，不是建议引入其整套系统。
- 对当前 Console 的可行性判断，已核对现有 `Home` 的 Task 森林构造与前端 API 字段。
- 新增原型使用虚构数据，只验证信息组织、视觉方向与基本交互，不证明真实数据规模下的性能、时间语义或生产可行性。

## 3. 重点项目对比

| 项目 | 主要表现层 | 最值得借鉴 | 不适合直接照搬 |
|---|---|---|---|
| Mission Control | Task pipeline、Run 检查、review、活动与成本面板 | 区分“执行过”和“已验收”；围绕负责人、失败、review 组织信息 | 面板数量和治理概念很多，全部引入会重新造成信息堆叠 |
| TeamClaude | Agent topology、list／kanban、消息流、review 轮次、replay | Agent 状态与 Task 执行放在同一上下文；review 轮次清晰可见 | 消息流、终端、拓扑、看板同时常驻会抢占主视线 |
| FlowBoard | 父子 Task、Kanban、领取租约、checkpoint、review、attention signal | 父 Task 聚合子 Task；用单一注意力信号突出需要介入的项 | Idea Canvas 面向规划，不等于执行监控；不应把创意画布直接变成 Task 首页 |
| Babysitter Observer Dashboard | Needs you／Working／Failed 等分栏、pipeline、breakpoint、raw JSON | 首页只回答“哪个 Run 现在需要我”；原始 JSON 收到详情层 | 以 Run 为中心，不完全匹配当前 Task → LOOP → Thread 模型 |
| Harmonograf | Agent Gantt 泳道、topology、Trajectory、Inspector drawer | 时间线看并发，Graph 看关系；点击后统一在抽屉看细节 | 依赖完整遥测 span；当前数据不足以直接复刻 |
| RLMFlow | live tree、Gantt、topology、逐步 viewer | 同一执行数据可投影为不同视图；树直接显示节点状态和结果摘要 | 其递归 Graph 是运行时一等数据，当前 Console 不应为了画图先重建一套图模型 |
| pi-subagents | 紧凑进度行、FleetView、可展开 Inspector | 默认只显示当前 Tool、最近输出、Token、耗时；需要时再展开完整流 | 终端交互与 TUI 信息密度不适合原样移植到 Web |
| VoltAgent Platform | Agent hierarchy、trace timeline、waterfall、成本归因 | 层级与 trace 分层；OUTPUT 与细粒度调用各居其位 | 完整 waterfall 需要更细的遥测数据，不能只靠现有 Task 列表生成 |
| LangAlpha | subagent 卡片、对话与产物、Sources 面板 | 每个 subagent 卡片只保留 Tool／Token 摘要；结果与证据可关联查看 | 对话、文件、市场图表是领域核心，不应挤入当前 Task 主视图 |
| openClaw-dashboard | Agent Strip、Task Kanban、Activity Feed、Metrics | 顶部 Agent 状态条与 Task 看板形成快速总览 | 项目自称实验性；拖拽 Kanban 与大量常驻面板不作为默认方向 |

来源：Mission Control。[GitHub](https://github.com/builderz-labs/mission-control)
来源：TeamClaude。[GitHub](https://github.com/albertnahas/teamclaude)
来源：FlowBoard。[GitHub](https://github.com/rasimme/FlowBoard)
来源：Babysitter Observer Dashboard。[GitHub](https://github.com/yoavmayer/babysitter-observer-dashboard)
来源：Harmonograf。[GitHub](https://github.com/pedapudi/harmonograf)
来源：RLMFlow。[GitHub](https://github.com/shyamsn97/rlmflow)
来源：pi-subagents。[GitHub](https://github.com/nicobailon/pi-subagents/blob/main/docs/observability.md)
来源：VoltAgent Platform。[GitHub](https://github.com/VoltAgent/ai-agent-platform)
来源：LangAlpha。[GitHub](https://github.com/ginlix-ai/LangAlpha)
来源：openClaw-dashboard。[GitHub](https://github.com/bokiko/openClaw-dashboard)

## 4. 可复用的表现层原则

### 4.1 首页突出“需要注意力”，不是“数据齐全”

当前 Task 数据很全，但每个节点同时展示标题、来源、状态、Token、时间、OUTPUT、LOOP、Thread、Agent 链接时，所有信息都在争夺注意力。

更合理的主次关系：

- **第一层**：状态、Task 标题、Agent、需要介入的原因。
- **第二层**：父子关系、子 Task 状态汇总、默认 LOOP、OUTPUT 摘要。
- **第三层**：完整 OUTPUT、竞争 LOOP（如有）、Thread、错误详情、原始 JSON。

这与 Babysitter 的“Needs you”和 FlowBoard 的单一 attention signal 方向一致。

### 4.2 层级、时间、关系是三种不同问题

| 要回答的问题 | 最合适的表现层 |
|---|---|
| 这个 Task 拆成了什么？哪个分支卡住？ | Task 树／执行地图 |
| 哪些 Agent 在并行？哪里等待最久？ | Agent 泳道时间线 |
| Agent 之间如何调用和转移？ | Topology／Graph |
| 这次执行具体调用了哪些 Tool？ | Trace／Thread 详情 |
| 哪个结果可以验收？ | Review 面板／详情抽屉 |

不要让一张图同时承担五种问题。

### 4.3 主面板摘要，详情按需加载

pi-subagents 和 Harmonograf 都体现了同一原则：主界面只保留紧凑状态，点击后再展开完整输出、payload 或技术细节。当前 Console 已完成长文本折叠，新视图应延续这一方向，而不是把更多正文塞回首页。

### 4.4 配置拓扑与执行状态应分开

即使后续增加 Graph，也应区分：

- Agent 定义之间“可以怎样协作”；
- 当前 Task 实例“实际怎样执行”。

当前需求属于后者，不需要先做一个可编辑 DAG 或工作流设计器。

## 5. 研究候选 A2：Task 执行地图

### 5.1 核心结构

```text
[列表] [执行地图]                         筛选：待领取 / 执行中 / 待验收 / 已结束

┌ 根 Task：整理季度业务复盘 ───────────── 待验收 · 5 Task · 18.4k Token ┐
│ Agent：commander                                  最新 LOOP #12 →       │
│ 子 Task：2 已结束 · 1 执行中 · 1 待验收                                │
│                                                                       │
│ ├─ ● 收集销售数据        analyst       执行中 · LOOP #8                │
│ ├─ ✓ 收集客户反馈        researcher    已结束                          │
│ ├─ ! 生成分析初稿        writer        待验收 · 查看 OUTPUT →           │
│ └─ ✓ 汇总风险清单        reviewer      已结束                          │
│                                                                       │
│ 当前关注：生成分析初稿有 1 份 OUTPUT 待验收                             │
└───────────────────────────────────────────────────────────────────────┘

点击任一 Task：右侧打开详情，不离开执行地图
┌ Task #42 ─────────────────────┐
│ 状态 / Agent / 最新 LOOP       │
│ CURRENT OUTPUT 摘要            │
│ [查看全部 LOOP] [打开 Thread]  │
└───────────────────────────────┘
```

### 5.2 它能直接回答的问题

- 哪个根 Task 正在推进？
- 它有哪些分支，哪个分支需要我？
- 每个分支由哪个 Agent 负责？
- 最新 LOOP 和 OUTPUT 在哪里？
- 哪条分支完成、执行中、待领取或异常？

### 5.3 与当前列表的关键区别

- 从“每个节点都是一张信息完整的卡片”改成“根 Task 是一个执行单元”。
- 子 Task 默认是一行摘要，不再默认展开 OUTPUT、Thread、Token 等全部字段。
- 整个分支的注意力状态在根节点聚合，例如“1 个待验收”“1 个异常”。
- 详情通过右侧抽屉或现有 Task 详情页打开，主视图保持可扫描。
- 已完成分支可以折叠，活动与异常分支优先展开。

### 5.4 数据与实现判断

现有 `parent_task_id` 已用于构造 Task 森林；列表还包含 `status`、`pool_status`、`agent_name`、`result_preview`、`latest_loop_id`、`latest_thread_id`、`tokens`、`created_at` 和 `finished_at`。这些信息足以支撑第一版执行地图。[api](../../console/frontend/src/lib/api.ts)

当前首页已经有 `buildForest`、`TaskNode` 和 `ReviewPanel`，可以在现有数据模型上演进，不需要先改后端。[Home](../../console/frontend/src/pages/Home.tsx)

注意边界：

- 第一版可显示“最新 LOOP”，不能在没有额外请求的情况下显示每个 Task 的完整 LOOP 历史。
- 子 Task 数量与状态可以直接聚合，但不要把“子 Task 完成比例”自动解释为整个根 Task 的真实业务进度。
- 第一版不需要自由拖拽、缩放、自动布局或 DAG 图库；语义化 HTML 树和连接线就足够。

**成本：中。直观性提升：高。后端改动：第一版可避免。**

## 6. 研究候选 B2：Agent 泳道时间线

### 6.1 核心结构

```text
时间 →        10:00        10:05        10:10        10:15
commander     [分派 Task]              [等待子 Task]        [汇总]
analyst                    [LOOP #8 · 执行中──────]
researcher    [LOOP #5 · 已结束────]
writer                                 [LOOP #9 · 待验收]

底部：Task 阶段条 / 当前注意 / 异常
点击一段：右侧显示 LOOP、Thread、OUTPUT、错误和 Token
```

### 6.2 优点

- 最适合观察并行度、空闲、等待、重试与长时间阻塞。
- 能把“Agent 正在做什么”与“做了多久”合并起来。
- 对后续性能分析和复盘很有价值。

### 6.3 当前限制

现有 Task 有 `created_at` 和 `finished_at`，LOOP 有 `created_at`，但这不等于完整的执行开始／结束时间，也没有每个 Tool、LLM 调用或 Thread 活动的统一 span。仅靠这些字段画出的条带容易把“创建到结束”误读成“实际运行时长”。

若要做可信的时间线，需要先明确并可能补充：

- Task／LOOP 的 `started_at`、`ended_at`；
- 等待、执行、暂停、重试等状态区间；
- 可选的 Thread／Tool span；
- 数据更新时间与实时刷新语义。

**成本：高。直观性提升：高，但偏诊断。后端改动：大概率需要。**

它既可作为 B 线的视觉状态页，也可作为后续诊断视图；是否进入实现由原型观察与真实时间数据成本共同决定。

## 7. 研究候选 A3：状态看板 + Task 对话工作区

### 7.1 核心结构

```text
[待领取 2]       [执行中 4]        [待验收 1]        [已结束 18]
┌ Task #31 ┐     ┌ 根 Task #40 ┐   ┌ Task #42 ┐     ┌ Task #20 ┐
│ 标题     │     │ 3/5 分支活跃 │   │ Agent    │     │ 已验收   │
│ 领取 →   │     │ 1 个异常     │   │ OUTPUT → │     │          │
└──────────┘     └──────────────┘   └──────────┘     └──────────┘

点击卡片：进入 Task 对话，继续发送指令；按需查看层级、LOOP、Thread 和 OUTPUT
```

### 7.2 优点

- 状态分布一眼可见，交互模式成熟。
- 卡片内容可以比当前列表更短。
- 适合把“待领取”和“待验收”作为主要工作队列。

### 7.3 缺点

- 父子层级会被状态列拆散；一个根 Task 的不同分支可能出现在多列。
- 与当前“待领取／执行中／待验收／已结束”筛选相比，主要是布局变化，信息理解提升有限。
- 若为了保留层级又把整棵树放进一张卡，看板会变得很高，失去简洁性。

**成本：低至中。直观性提升：中。后端改动：基本不需要。**

如果首要目标是快速降低操作成本，它可能优于层级视图；是否如此，需要通过同一组定位与 OUTPUT 操作任务验证。

## 8. 三个原始研究候选的适配比较

此表只比较数据与问题适配，不代替六视图实测，也不跨 A／B 两线评选总冠军。

| 维度 | A2 Task 执行地图 | B2 Agent 泳道时间线 | A3 状态看板 + 抽屉 |
|---|---:|---:|---:|
| 看清父子层级 | 强 | 弱 | 弱至中 |
| 找到需要注意的 Task | 强 | 中 | 强 |
| 观察并发与等待 | 中 | 强 | 弱 |
| 查看 OUTPUT 与验收 | 强 | 中 | 强 |
| 与现有数据匹配 | 高 | 低至中 | 高 |
| 第一版实现成本 | 中 | 高 | 低至中 |
| 信息过载风险 | 低至中 | 中 | 低 |
| 主要角色 | A 线层级候选 | B 线时间候选 | A 线队列候选 |

## 9. 不建议的方向

### 9.1 默认全局 DAG

当前是 Task 森林，不是任意依赖图。把全部根 Task、子 Task、Agent、LOOP、Thread 放进一张自由布局 DAG，会带来：

- 交叉边与布局抖动；
- 节点缩放后文字不可读；
- 状态、层级和调用关系混在一种边上；
- 用户需要先理解图，才能找到原本只需一行说明的异常。

未来若确有跨 Task 依赖或 Agent transfer 数据，可以在单个根 Task 内增加“关系视图”，而不是作为全站默认首页。

### 9.2 A 线首屏常驻完整消息流

TeamClaude 和 LangAlpha 的消息流适合深入观察，但 A 线 Task 首页已经有层级、OUTPUT、LOOP 和验收，再常驻完整消息流会重新制造信息表达问题。完整消息流只在 A1 的 Task 工作区按需展开；新 B 完全不含消息流。

### 9.3 为了视觉效果引入重型图编辑能力

拖拽连线、节点编辑、自由画布、minimap 和复杂缩放不是当前问题的核心。新 B 不引入 3D 或图编辑器；Phaser 4 只承担 2D 游戏场景，高密度对话与审批仍只由 A1 的 DOM 工作区承担，避免为了视觉继续堆叠技术复杂度。

### 9.4 把完成百分比当成真实进度

子 Task 状态可以统计，但 Agent 的 Task 拆分数量不代表工作量均匀，也不代表根 Task 的业务完成度。优先显示明确的状态计数，不显示没有依据的“73% 完成”。

## 10. 双视图 dummy 原型

原型目录：`prototypes/agent-fleet-presentation/`。A1 与新 B 共享同一份数据：6 个 Agent、8 个 Task，覆盖执行中、待领取、待验收、异常、已结束与不同认领者竞争。原型支持 LIVE／PAUSED、hash 路由、全屏及桌面／移动布局。

### 10.1 与现行代码对齐的信息模型

本版先核对 ADR-0010／0012／0028／0030、Control Plane 的 `pool.py`、`dispatcher.py`、`main.py`、Fleet `agent_spawn`、DeerFlow 原生 `task` 以及现有 Console，再重建 dummy data。共同基线不是“每个 Task 常驻多个 LOOP”，而是：

1. **主树按 `parent_task_id` 组织父子 Task。** Fleet `agent_spawn` 创建子 Task；每个子 Task 默认再拥有自己的 LOOP 与 Thread。
2. **默认路径为 1 Task → 1 LOOP → 1 Thread。** Task #101 只有 LOOP #12 与 Thread `c41f9a2e`；退回后的 `v2` 和 3 次 RUN 都留在同一 LOOP／Thread，不再虚构 LOOP #11。
3. **额外 LOOP 只表示不同 claimant 的竞争领取。** Task #201 单独提供 scout 的 LOOP #7 与 analyst 的 LOOP #13，两个 Thread 和 OUTPUT 可切换比较；这些信息只在 A1 工作区展示，B 不显示竞争 LOOP。
4. **DeerFlow 原生 `task` 是 Thread 内部委托。** Task #101 的原生 subagent 在父 Thread 的消息详情中折叠展示，不进入 Fleet Task 树，也不生成新的 Fleet LOOP 或业务 Thread。
5. **视觉不篡改业务关系。** B 的办公室位置不代表继承、执行顺序或真实物理协作；角色浮窗只读取对应 Task 的摘要，完整工作区仍在 A1。

### 10.2 状态与工作区彻底分工

A1 点击任一 Task 进入统一工作区：默认执行条显示 LOOP、version、RUN 与 Thread；中间是选中 LOOP 的 Agent 实例、消息、Tool JSON 折叠与指令输入；右侧是 Token、OUTPUT 和原始 JSON。指导、退回、重试、采纳 OUTPUT 与完整 Thread 也都留在 A1。

新 B 不复制这些功能。它展示 6 个角色的 WORKING／OUTPUT／ALERT 状态与工位动效；点击角色后保留游戏与全屏状态，只在 DIV 浮窗中显示 Agent、Task、LOOP／Thread 和当前 OUTPUT 摘要。B 不提供输入框、频道切换、完整 Thread 或审批按钮，因此不会形成两套需要同步的工作区。

> 修正记录：初版缺少对话；第二版把 LOOP 压成元数据；第三版错误地为同一 commander 虚构顺序 LOOP，并把原生与 Fleet subagent 混为一谈；第四版又让 B 同时承担完整对话与审批。最新版本保留已核实的信息模型，并明确 B 的简洁来自职责分离，而不是删除用户需要的功能；摘要浮窗只是减少场景切换，不把 A1 搬回游戏。

| 评估线 | 原型 | 主要观察点 |
|---|---|---|
| A 信息与操作 | A1 紧凑列表 + Task 工作区 | 信息效率、完整详情与全部操作 |
| B 视觉状态 | Phaser 4 纯游戏 Fleet 指挥室 | 游戏感、队伍在场感、地图移动、状态辨识度与摘要浮窗 |

原型只改变表现层，不接入真实 Console；不验证真实模型回复、领取、验收、退回等业务操作。

## 11. 当前评估重点

### 11.1 A 线：信息效率

A1 保持现状，后续以它为基线继续观察：

1. 找到异常 Task、待验收 Task 所需的时间与点击次数。
2. 能否说清根 Task、子 Task，以及 Task、LOOP、Agent 实例的归属关系。
3. 从发现注意点进入默认 Thread、补充指令和查看 OUTPUT 的成本。
4. 首屏是否突出重点，是否还有重复、过强或不必要的说明。
5. 真实数据接入成本，以及大规模 Task 下的折叠、筛选和滚动风险。

### 11.2 B 线：纯游戏状态

1. 是否真正产生“队伍正在协作”的在场感，而不只是换皮。
2. WORKING／OUTPUT／ALERT 是否能通过角色与动效直观区分。
3. 点击角色的游戏内浮窗是否自然，Agent、Task 与 LOOP 上下文是否准确。
4. 拖拽、缩放、复位、LIVE／PAUSED、全屏与移动端是否保持稳定。
5. 真实 Agent 数量增长后，场景布局、角色辨识度与渲染性能是否可控。

“是否更愿意让它持续开着”是有效评分项，不必伪装成纯理性指标。B 的游戏化不再以承担对话和审批为目标，完整信息与操作始终由 A1 保证。

### 11.3 当前决策状态

**原型方向已收敛为 A1 与纯游戏 B，真实 Console 尚未改动。** A1 不再继续扩展候选；下一轮重点体验 B 的 Phaser 4 无限草地、全屏状态表现与游戏内摘要浮窗，再决定它如何成为独立新页面，以及后续优先补充哪些游戏交互。

## 12. X 评价补充与结论收敛

### 12.1 证据边界

补充检索覆盖了项目名、精确 GitHub 仓库名、界面关键词、作者发布帖和部分高互动帖回复，并区分热门与最新结果。

X 样本有三个明显限制：

- 多数可见内容是项目作者发布、项目推介或 README 转述，不等于真实使用评价。
- TeamClaude、Mission Control、Harmonograf、openClaw-dashboard 均出现重名或指向不同项目的结果，不能混用。
- 多个小型项目没有检索到可核验的界面讨论。没有检索到不代表没有评价，只代表本轮无法据此判断。

因此，本节不做情绪百分比、互动量排名或“社区普遍认为”的推断，只判断这些帖子能否改变前述方案。

### 12.2 可用证据及其权重

| 项目与帖子 | 实际内容 | 证据分类 | 对方案的影响 |
|---|---|---|---|
| Mission Control，作者发布 | 作者强调用一个自托管 dashboard 跨终端运行多项业务，涵盖 Task 分派、review、成本、schedule 等 | 作者自述，不是独立评价 | 说明“集中状态与控制”是核心卖点，不能证明 32 个面板或某种布局更好 |
| Mission Control，第三方项目帖 | 明确写有“From the README”，逐项整理 Tasks、Operations、Knowledge、Governance | README 转述，排除为 hands-on 评价 | 不用于判断界面优劣 |
| VoltAgent／VoltOps，作者 dashboard 发布 | 展示 Run Count、Trace Latency、LLM Latency 等图表，目标是发现慢步骤、噪声 Tool 和瓶颈 | 作者产品说明 | 支持把时间线和图表定位为诊断工具，而不是证明它们应成为 Task 首页 |
| VoltAgent，第三方短评 | 称框架“ I'd underrated”，列举 supervisor／subagent、MCP、memory 和 VoltOps | 独立评价，但没有界面使用细节 | 只能作为弱正面信号，不能评价 Graph 或 Timeline |
| pi-subagents，FleetView 推介 | 具体提到终端底部持续显示运行中 Agent 的进度与 Token，以及运行中纠偏 | 界面相关推介，未说明使用时长 | 与“持续、紧凑的运行摘要”方向一致 |
| pi-subagents，功能推介 | 强调 persistent widget、live task visibility、FleetView 和按需打开运行中对话 | 功能推介，不是独立对比测试 | 支持主摘要与详情分层，不支持增加更多常驻面板 |

来源：Mission Control 作者发布。[X](https://x.com/nykdotdev/status/2075184643171819572)
来源：Mission Control README 转述帖。[X](https://x.com/rosehexon/status/2103243136797684010)
来源：VoltAgent dashboard 发布。[X](https://x.com/voltagent_dev/status/1995898662690705731)
来源：VoltAgent 第三方短评。[X](https://x.com/node2040/status/2029896742389375219)
来源：pi-subagents FleetView 推介。[X](https://x.com/LiuweijiaVip/status/2090361654231888173)
来源：pi-subagents 功能推介。[X](https://x.com/DanKornas/status/2110539799174004804)

### 12.3 未形成有效界面证据的项目

- **TeamClaude**：精确仓库名与 `TeamClaude sprint` 未得到可用帖子；泛项目名结果指向其他同名仓库，已排除。
- **FlowBoard／Babysitter Observer Dashboard／RLMFlow**：本轮未检索到可核验的 X 界面评价。
- **Harmonograf**：结果主要是同名机械绘图装置，与 Agent 项目无关。
- **openClaw-dashboard**：结果主要讨论 OpenClaw 本体或其他 dashboard，不能归因到目标仓库。
- **LangAlpha**：可见结果以官方发布和非界面话题为主，没有形成对 subagent 卡片或 Sources 面板的独立评价。

### 12.4 两条指定参考带来的最终收敛

Morlex 帖的可见正文强调多个拥有各自 memory、skills 与 tools 的 Bot 组成团队，协同设计、编码、测试并交付工作；它支持“队伍协作”的心智，但正文本身不能证明某种界面布局更优。[X](https://x.com/0xMorlex/status/2115872777660141771)

Dallas Tesla 帖的可见正文描述了一个与真实 Bot 团队状态联动的虚拟办公室：Bot 接到 Task 后走到工位，协作时移动到其他工位或 lounge，向 chief of staff 交付时进入其办公室。作者同时明确说这些表现并非必要，价值在于“看着工作完成很有趣”。这里借鉴的是角色在场感与状态反馈，不把移动路径当成业务事实。[X](https://x.com/dallastesla/status/2115536116162216197/video/1?s=46)

由此形成最新方案：

1. **A 线只保留 A1。** A2、A3 与旧 B 三个视图不再分散体验判断；完整工作区继续保证人的指导、退回、重试与 OUTPUT 采纳。
2. **B 线只做纯游戏状态。** Phaser 4 无限草地办公室负责队伍在场感、状态与动效，不再复制 A1 的频道、完整 Thread 或审批。
3. **角色点击不再打断游戏。** 任一角色只打开游戏内摘要浮窗，保留当前地图视角与全屏；需要完整对话和操作时显式返回 A1。
4. **视觉隐喻不改变技术事实。** 不把 Agent YAML 画成继承树，不把兄弟 Task 画成顺序链，不用动效虚构进度，也不合并不同实例的真实 Thread。

因此当前只评估 **A1 工作区与 Phaser 4 纯游戏 Fleet 指挥室**，不再回到旧六视图之间做选择。

## 13. B 的业务语义与状态动线落地

### 13.1 游戏元素只表达已确认的概念

- **人物 = Fleet Agent 身份；LOOP = 当前执行实例；Thread = 这次执行的对话与工具上下文。** 人物不是 Task，也不按 RUN 历史复制。
- **Task = 工作与验收单元，不是对话。** 中央 Task 大屏只提供工单状态；人物漫画气泡只显示 `··· Task 数量`，不再用进展文字遮挡场景。
- **工位 = 当前 LOOP／Thread 的工作位置。** 桌子不表示 DeerFlow Project；电脑只是工具执行与文件生产的视觉道具。
- **OUTPUT 交付不等于 Task 完成。** 人物把 OUTPUT 送到 commander 的交付区后仍须验收；退回必须由同一人物回到原工位，复用原 LOOP／Thread。
- **Fleet subagent 与 DeerFlow 原生 subagent 不混用。** 本轮没有为原生 Thread 内委托新增 Fleet 常驻人物，也没有虚构 Project 区域。

### 13.2 可重播的 dummy 状态演示

B 新增中央 Task 大屏、6 个人物数量气泡、OUTPUT 纸张与 commander 交付区，并循环演示真实业务顺序：

1. writer 携带 OUTPUT 到交付区，验收采纳后离开工位休息。
2. 空闲 researcher 前往 Task 大屏领取未分配的 Task #301，演示生成 LOOP #14 与 Thread `demo301a`。
3. researcher 回到工位执行，提交 OUTPUT 后进入待验收。
4. 第一次交付被退回，同一 researcher 返回原工位补充证据，LOOP 与 Thread 不变。
5. 第二次提交验收通过，人物离开工位休息；reviewer 始终留在原工位表达异常，不用随机闲逛伪装活动。

“重播动线”可恢复初始状态。人物可直接拖拽以避免遮挡，拖拽不改变业务工位，也不会误开浮窗。点击人物先打开其负责的 Task 列表（包含不同 Task 的竞争 LOOP），再点击 Task 打开具体对话工作区；Task 大屏仍保留游戏内摘要浮窗。

### 13.3 验证范围

- 保留并通过原有 A1、全屏、手机、浮窗、相机、LIVE／PAUSED 与无限草地验证。
- 新增 `validate-flow.mjs`：检查领取、实际移动、交付、退回前后 LOOP／Thread 身份不变、验收、重播、Task 大屏浮窗、人物拖拽、数量气泡和 Task 列表进入对话。
- `npm run validate` 依次运行通用回归与状态动线专项回归；`npm run validate:flow` 可只跑后者。
