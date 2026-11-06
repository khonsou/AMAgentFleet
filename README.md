# AMAgentFleet：小公司的 Agent 经营舰队

**让小公司无需组建平台团队，只要注册 Agent、赋予 Goal，就能复用现有 Agent／VM 能力，在统一任务池与共享证据上，持续完成可验证的数字经营工作。**

平台通过**统一协议层与 RuntimeAdapter**复用 DeerFlow、DSH、Grok Bots 等 Agent／VM 的基本能力，提供统一 Task Pool、多 Agent 协作、共享 Evidence、统一授权和傻瓜友好的 Playground。GUI、第三方 Agent 与平台内置 Agent 使用同一套 operation 契约；更换底层 Runtime 时，不需要重写这些客户端与业务流程。

非工程用户不需要理解 Loop、Thread、Checkpoint、Runner、PAT 或 MCP，也能发起工作、观察真实进展、查看证据并在异常时介入。

**数字工作默认 Agent 执行；实物放行、资金／物料、生产发布和公开承诺由具名人类负责。**项目追求的不是“无人公司”，而是极少核心人员拥有更强、可验证、可恢复的数字执行力。

> **上游与部署**：本项目基于 [DeerFlow](https://github.com/bytedance/deer-flow)（字节跳动开源，MIT）二次开发，执行底座（网关）来自 fork [khonsou/deer-flow](https://github.com/khonsou/deer-flow) tag `am-fleet-base-v2`（包含 Fleet 的 `skill_evolution` 支持）。模型默认使用 DeepSeek 官方 API（OpenAI 兼容协议）；RuntimeAdapter 用于复用和替换执行能力，不改变既有安全边界。部署见 [deploy/DEPLOY.md](deploy/DEPLOY.md)。

## 1. 项目目标与边界

### 1.1 用户要完成的事

1. **注册能力**：用 YAML／模板注册 Agent、工具、模型 Profile 和授权，而不是为每项工作重新开发框架。
2. **统一接入**：GUI、第三方 Agent 和内置 Agent 通过统一协议调用相同能力，共享鉴权、幂等、错误、确认与审计语义。
3. **更换执行底座**：按 capabilities 选择或更换 DeerFlow、DSH、Grok Bots 等 Runtime，保留冷切换预检与回滚，不迁移或伪造不支持的能力。
4. **赋予目标**：用 Goal 描述结果、约束、反指标、期限和停止条件，而不是只发一句聊天指令。
5. **持续协作**：多个 Agent 在统一 Task Pool 中分工、并行检索、交付、互相纠错，并自动进入必要的下一轮。
6. **共享事实与授权**：所有相关 Agent 读取同一 Evidence 版本，通过 Gateway／Vault 复用业务系统接入；不依赖私有记忆转述，也不需要重复鉴权。
7. **简单操作**：Playground 只要求用户理解 Goal、Task、Evidence、Effect；工程实现继续留在控制面与观测页。
8. **证明价值**：同时看到结果、证据、总成本、人类时间、权限和恢复记录，而不是只看 Agent 数量、Token 或任务数量。

### 1.2 四个业务对象

| 对象 | 回答的问题 | 最小内容 |
|---|---|---|
| **Goal** | 为什么值得做，何时停止 | 目标结果、约束、反指标、期限、停止条件 |
| **Task** | 下一步做什么才算完成 | 最小可验证工作、依赖、Owner、验收标准 |
| **Evidence** | 凭什么相信 | 源记录、版本、交付、外部 Ground Truth、独立验证 |
| **Effect** | 谁以什么权限改变了世界，结果如何 | 动作、授权、承诺人、幂等、回执、恢复 |

Loop、Thread、Checkpoint、Runner、Profile、PAT、MCP、FAP 是实现细节，不进入经营层日常语言。

### 1.3 统一协议、Runtime 与三个内置 Agent

| 平台层 | 统一职责 |
|---|---|
| **统一协议层** | 以机器可读 operation ID 暴露 Task、Agent、Thread、Run、Evidence 与平台操作；GUI、第三方 Agent、指挥官、操作助手和评测 Agent 共用契约；浏览器不能绕过鉴权或调用 Runner 特权接口 |
| **RuntimeAdapter** | 将 Agent 部署、Thread／Run、恢复引用、模型目录与 capabilities 标准化；支持 DeerFlow、DSH、Grok Bots 等执行底座的接入、冷切换与回滚 |
| **Runtime Capability Plane** | 用户或第三方 Agent 在运行时注册 Provider／operation／Artifact；所有 Agent 默认 `inherit_all` 并继承新能力，通过统一 invoke、共享 Workspace 和 DAG 混合编排；不包含业务网站适配（ADR-0042） |
| **Playground** | 为上述统一能力提供简单 GUI，不为页面另造特权业务 API |

| 内置 Agent | 职责 | 边界与状态 |
|---|---|---|
| **`fleet-cmd-01`（舰队指挥官）** | 感知 Agent 与能力、拆解 Goal、直接调用或混合编排、共享 Evidence 并恢复 | 平台能力放大器；默认继承全部运行时能力，不限制业务或 subagent |
| **`fleet-ops-01`（舰队操作助手）** | 处理平台操作与业务 Goal，发现、调用、组合和恢复运行时能力 | 平台能力放大器；与指挥官和普通 Agent 共用 Capability Plane |
| **`fleet-eval-01`（平台自迭代评估 Agent）** | 发布前执行平台评测并评估开发效率；发布后注册并持续只读监控运行状态，依据项目 Goal 与平台评分总结后续迭代方向 | 不评价第三方业务 KPI，不修改题目／阈值，不自我验收，不自动合入代码；规划新增，分数由确定性评分器计算 |

`fleet-eval-01` 有两种工作模式：**开发评测**在 PR、发布候选或人工触发时运行，比较候选版本与基线，评价功能结果、回归、Token／时间／人类介入和返工等开发效率；**运行监控**在项目发布后持续读取 Task／Loop／Run、错误、延迟、成本、恢复和 Playground 反馈，按周期或异常触发模型总结，形成平台 know-how 与下一轮开发建议。指标采集可以持续，模型分析按需要触发，避免无意义地持续消耗 Token。

它可以把建议转成开发 Task 候选，但必须由维护者评审后进入开发；生产环境只做只读监控与报告，不执行负载测试，不自主修改平台代码，也不把私有 deermem 记忆作为长期评测档案。

### 1.4 非目标

- 不再造一个通用 Agent 框架，优先复用已有 Agent／VM 能力。
- 不以 Project 容器、统一向量库或复杂上下文装配器作为前提。
- 不把 Agent 数量、Task 数量、Token、PR／内容数量或自主率当作经营成果。
- 不追求全公司、全任务、全系统自动化；物理动作和公开承诺不默认放权。
- 不用模型自评替代源系统、实物、真实用户行为或独立验证。

## 2. 参考的最佳实践与采用原则

| 实践 | 项目采用的原则 | 不照搬的部分 |
|---|---|---|
| [OpenAI Harness engineering](https://openai.com/index/harness-engineering/) | 共享、版本化、可读的仓库知识是系统记录；人类设计环境与反馈回路 | 不把所有经营任务都当成可自动测试的 coding 任务 |
| [OpenAI Symphony](https://openai.com/index/open-source-codex-orchestration-symphony/) | 统一任务池作为控制面；给 Agent 目标而非僵硬状态机 | 不用任务吞吐量替代结果质量与净价值 |
| [Anthropic Agent Teams](https://code.claude.com/docs/en/agent-teams) 与[长任务 Harness](https://www.anthropic.com/engineering/harness-design-long-running-apps) | 独立执行上下文、共享任务与产物、生成与独立评价分离、自动迭代 | 不默认多 Agent；完整 Harness 可能超过 20 倍成本，必须保留单 Agent 对照 |
| [Vercel d0 simplification](https://vercel.com/blog/we-removed-80-percent-of-our-agents-tools) | 事实已经可读时，Model＋文件／证据＋Goal 优先；删除无收益工具 | 不在源数据混乱时用“直接读取”掩盖数据治理问题 |
| [Cognition：Don’t Build Multi-Agents](https://cognition.ai/blog/dont-build-multi-agents) | 强依赖工作必须保留相关决策与完整上下文，避免机械拆分 | 不把摘要当成所有场景的充分交接 |
| AM 三轮对抗研究（见[方向决策](docs/reviews/2026-11-01-goal-driven-shared-evidence-direction.md)） | Goal／Task／Evidence／Effect；数字世界快跑，物理世界慢承诺；一个真实闭环先行 | 不做 217 节点全量机器化、三个平行试点和无证据扩张 |

**总原则：复制不变量，不复制组织外形；每个角色、工具、Skill、SOP 和中间层都必须用实测证明增益，否则删除。**

## 3. 开发与测试方向

| 主线 | 开发重点 | 必须通过的测试 |
|---|---|---|
| 统一协议层 | 统一 operation ID、读写、确认、幂等、错误、审计与能力发现，服务 GUI、第三方和内置 Agent | 三类客户端调用同一 operation 行为一致；浏览器无特权旁路；human-only 与秘密边界不变 |
| 统一任务池与 Goal 闭环 | Task 依赖、自动续跑、Goal 复测与停止、任务去重和过期 | 子任务交付自动唤醒父 Thread；无人工催促完成两轮；达到停止条件后不再烧 Token |
| 多 Agent 协作 | 指挥官发现 Agent、并行检索、组装、交叉校验、独立 Evaluator | 真实多源任务；不预置 worker 输出；错误能被发现；与单 Agent 比较质量和成本 |
| 共享 Evidence 与统一授权 | 版本化事实、观察、交付、验证与引用；Gateway／Vault 统一使用权限 | 同一证据版本可复现；秘密正文不进入 Task／Thread／日志；未授权 Effect 为零 |
| 可复用与可替换 Runtime | DeerFlow 默认，DSH／Grok Bots／其他 VM 按 capabilities 接入；冷切换与回滚 | Runtime Contract、恢复、错误分类、能力不支持显式返回；客户端与业务流程无需重写 |
| Playground | 注册 Agent、赋予 Goal、启动、观察证据、处理异常与承诺 | 非工程用户独立完成核心路径；不需要理解 Loop／Thread；失败可诊断、可停止 |
| 三个内置 Agent | 指挥官负责业务编排，操作助手负责平台操作，评测 Agent 负责平台评估 | 三者权限隔离；操作助手不承接业务，评测 Agent 不评价第三方业务、不自我验收 |
| 平台自迭代评测 | 固定平台验收面板、技术基线、评分卡、回归历史、组件删减 | 每次候选变更产生可比评分；硬门退化即拒绝；无增益组件删除 |

### 北极星指标：每人类小时交付的已验证 Goal（VGH）

平台自迭代以 **Verified Goals per Human Hour（VGH）** 为北极星，而不是以 Agent／Task／Token 数量或七维评分本身为目标：

```text
VGH = 60 × 独立验收通过的 Goal 数 ÷ 主动人工分钟
```

- **已验证 Goal**：执行前冻结结果、约束和停止条件，最终由确定性检查、源系统 Ground Truth 或独立 Evaluator 验收；Agent 自评不计。
- **主动人工时间**：包括配置、发起、补充提示、人工催促、查证据、修复输出、重新运行、异常恢复和验收；Agent 等待时间只计入墙钟。
- 失败、超时、未独立验收或只有部分产物的 Goal 不计入分子；同一 Goal 返工多次仍只计一次，子 Task 不得冒充 Goal。
- 主动人工时间未记录时，VGH 必须为空并标记缺少测量，不得按 0 分钟计算无穷大。
- 比较必须使用同题、同数据、同模型 Profile、等价预算和相同独立验收口径，防止靠拆小 Goal 或降低难度刷高指标。
- 七维评分是诊断与发布门，VGH 是价值方向；每次实验必须说明对 VGH 的预期影响，同时不得降低安全、证据和质量护栏。

当前 `core-v1` 只计算平台参考 Goal；真实 GEO Canary 单独记录业务验收、平台执行证据和 VGH，第三方业务 KPI 不混入平台评分。

### 当前开发切片：第三方可自动执行的最小闭环（ADR-0038）

2026-11-02 的 GEO 第三方真实集成报告证明：注册和单题执行已经打通，但完整多题闭环被接入语义阻塞。平台当前优先修复接入契约，不扩大 GEO 面板、不启用 cron，也不把第三方业务结果计入平台评分。

| 报告问题 | 本批实现 | 验证边界 |
|---|---|---|
| write PAT 可写不可读 | write 隐含 read；独立 read PAT 保持只读 | 本地真实单 PAT 读取、派发与批量收集已通过；生产部署后复测 |
| `claimed + error` 无法识别终态 | 增加结构化执行状态、终态、错误、重试和 Worker 投影；无恢复动作的错误明确为 failed | 永久失败、重试耗尽、自动重试与正常排队均有专项测试 |
| 逐 Task 轮询触发 429 | `GET /api/tasks?ids=...` 单次批量读取 1–100 个 Task | 实际 PG 查询、参数化与上限已验证；保留现有 60 req/min |
| close 只归档、不停 Run | close 等待 Runtime 确认取消后才归档；失败、不支持或 202 未确认均不归档 | 本地真实活动 Run 已进入 interrupted；生产槽位释放仍待在线验收 |
| 路由、版本与能力不透明 | `/api/meta` 返回部署版本、权限矩阵、真实 capabilities 和当前进程公开 operation | 不包含秘密；公开面仅限 Control Plane 稳定 API，嵌套 Router 经 OpenAPI 展开核对 |

本地可重复验收：`bash scripts/test_thirdparty_access_live.sh`（不调用模型）与 `bash scripts/test_task_close_live.sh`（调用一次本地配置的模型）。两者都会创建临时数据并自动清理。

**紧接着的生产联合验收顺序**：在当前部署上重跑单个 write PAT 全链路与幂等回放／冲突；制造一次 Provider 失败和一次主动 close，核对结构化失败、底层 Run 停止与槽位释放；最后才复测第三方六题首轮与独立核验。真实线上结果未返回前，不把本地通过写成生产闭环完成。

**后续已登记但未混入本批**：Runner 的 `max_wall_seconds`／工具调用硬预算与轨迹、Worker／queue position 可观测、`agent_config_version` 暴露。Playground 主观易用性验收继续等真实用户反馈。详见 [ADR-0038](docs/adr/0038-thirdparty-execution-access-semantics.md)。

### 平台评测切片：`fleet-eval-01` 与核心评测面板

**只保留 `fleet-eval-01` 一个评测身份**：开发阶段由它执行核心评测场景，部署后由同一个 Agent 持续监控运行状态。核心评测场景只是它读取的版本化配置，不是另一个 Agent，不是 `fleet_task_loops` 中的一种执行实例，也不是第三方业务 Goal。

开发阶段的参考测试会真实调用平台的 Task／Loop／Thread 作为探针：

1. 准备两个只读测试 Agent、受控数据源和可核验的预期事实。
2. 给指挥官一个多源参考任务，由它自主选择 Agent 并创建真实子 Task／Loop。
3. 验证子任务交付能否自动唤醒父 Thread，指挥官是否读取真实 OUTPUT 并正确组装。
4. 由独立评价与确定性检查核对事实、来源、停止条件和副作用；不能靠人工催促，不能无限创建后续任务。
5. 将耗时、Token、错误和协作证据计入平台评分。

它回答的是“平台是否具备自主多 Agent 编排能力”，不回答任何第三方业务是否成功。`fleet-eval-01` 负责组织测试并生成平台评分卡；确定性评分器负责算分。它不得修改参考面板或验收自己的评测结果。

**首批实现状态**：`core-v1` 场景、受控事实、确定性评分 CLI、建议状态机和专项测试已落地；`fleet-eval-01` 已注册但专用模型执行尚未激活；报告／建议表与统一协议 API 已完成。真实证据报告 `report_id=1` 为 **44 分、未评级**；实现子任务自动唤醒与执行预算后，`report_id=2` 为 **58 分、未评级**，P0 已通过且父 Loop 自动进入 `version=2`。Timeline 访问沿用现有 YAML 配置，不再新增专用代理；生产持续监控和 Evaluations GUI 尚未完成，当前平台评级因此仍为未评级。

**手动启动器实现状态（2026-11-02，ADR-0044）**：`single`／`multi` 启动器、隔离评测 Agent、VGH 测量、手动 `POST /api/eval/runs`、实施与候选报告关联已完成，并通过离线契约测试。当前只证明执行骨架可用；真实单 Agent 对照基线、真实模型解释和候选复测尚未执行，不得据此上调当前 58 分或未评级状态。

`geo-evidence-01` 等第三方 Agent 可作为后续接入 canary：平台只验收注册、执行、恢复、隔离、证据和权限契约是否可复用；其 GEO 目标、事实评分和业务迭代由第三方运营方自行负责，不进入 AMAgentFleet 的项目完成度评分。

近期不做：Project MVP、通用 Context Assembler、无真实需求驱动的通用 `task_search`、更多专职角色和更多连接器。

## 4. AMAgentFleet 项目完成度：硬门＋100 分

### 4.1 先区分两套评估

| 维度 | 平台开发评估（本评分） | 第三方运行评估（不属于本评分） |
|---|---|---|
| 评价对象 | AMAgentFleet 的统一协议、任务编排、多 Agent 协作、Runtime、共享 Evidence、Playground、安全与成本 | 第三方 Agent 的业务 Goal、事实质量、业务结果与自有停止条件 |
| 评价Owner | AMAgentFleet 维护者 | 第三方 Agent 运营方 |
| 评测执行者 | `fleet-eval-01` 组织评测，确定性评分器算分，独立证据复核 | 第三方自己的 Evaluator 与业务验收机制 |
| 主要证据 | 平台参考负载、契约测试、真实协作链、非工程用户测试、技术基线 | 第三方业务源记录、Ground Truth、经营指标与领域 Eval |
| 对发布的作用 | 决定平台能否发布、合并或进入下一阶段 | 决定第三方 Agent 如何迭代，不直接决定平台分数 |
| `geo-evidence-01` 的角色 | 仅作为第三方接入与能力复用 canary | 其运营方独立评价 GEO 闭环与业务价值 |

相关对象的实体归属与界面位置如下：

| 对象 | 本质 | 在哪里可见 | 能否作为业务 worker 被指派 |
|---|---|---|---|
| `fleet-eval-01` | 系统内置 Agent 定义 | **Agent Registry** 中以“平台评测”系统身份只读展示；从评测入口启动 | 不能承接普通业务 worker Task；开发／评测环境执行测试，生产环境持续只读监控 |
| `fleet_task_loops` | `fleet_task_loops` 表中的真实执行轮次记录 | Task 详情的 Loop 历史与关联 Thread | 不注册；由 Task 被领取时创建，也是 `fleet-eval-01` 分析开发评测与运行状态的证据 |

未来的 **Evaluations** 页只按 `fleet-eval-01`、场景版本和运行批次展示评测历史；场景配置是内部版本化输入，不作为独立 Agent 或额外产品身份注册。

`fleet-eval-01` 的 OUTPUT 按用途持久化，而不是只放在对话或私有记忆中：

| 内容 | 持久化位置 | 说明 |
|---|---|---|
| 评测场景与评分规则 | `evals/platform/`（源码版本化） | 定义“怎么考”，不保存运行输出 |
| 每次评测的真实执行与交付 | 现有 `fleet_task_loops.deliverable`、Thread／Run | 证明实际怎样执行，可恢复、可审计；不是开发计划表 |
| 结构化评分卡与版本对比 | `fleet_eval_reports`（已落库） | 保存平台版本、场景版本、各维得分、基线、证据引用和时间 |
| 迭代建议与处理状态 | `fleet_eval_recommendations`（已落库） | 保存最弱项、建议实验，以及 proposed／accepted／rejected／implemented／verified／closed／failed 状态和关联证据 |
| 完整报告与大型证据文件 | `artifacts/platform-evals/`（开发环境）或对象存储（部署环境） | 数据库只保存摘要与引用，不重复保存全量 transcript，不保存秘密正文 |

### 4.2 平台硬门：不满足就不能称为平台达标

- **P0 平台闭环**：标准参考负载完成多 Agent 协作、自动唤醒、独立评价与停止，无需人工催促，不是单次聊天或预置输出。
- **P1 协议、Runtime 与安全**：统一协议客户端、单元测试和 Runtime Contract 全过；无越权 Effect，秘密不进入 Task／Thread／日志，停止、冷切换回滚与恢复可用。
- **P2 核心用户路径**：非工程用户能通过 Playground 注册 Agent、赋予 Goal、启动、观察证据并处理异常，无需理解内部实现概念。
- **P3 可比基线**：平台参考负载与评分口径版本化，并能与上一版本或单 Agent 技术基线比较；缺少可比基线时最高 74 分。

### 4.3 平台评分卡

每个维度按 0–5 分锚定，维度得分＝权重 × 等级 ÷ 5。`fleet-eval-01` 只负责组织评测和解释证据，分数由确定性评分器计算；**不允许模型给自己打分，也不采用第三方业务 KPI 代替。**

| 维度 | 权重 | 主要证据 |
|---|---:|---|
| **Task Pool 与 Goal 编排** | 20 | 依赖、认领、去重、自动唤醒、复测、恢复、取消与停止条件 |
| **多 Agent 协作** | 20 | Agent 发现与选择、并行检索、组装纠错、独立评价、失败处理与摘要纪律 |
| **统一协议层** | 15 | GUI／第三方／内置 Agent 契约一致、幂等、鉴权、错误与审计、无特权旁路 |
| **Runtime 复用与替换** | 10 | Runtime Contract、capabilities、行为等价、接入成本、冷切换与回滚 |
| **共享 Evidence 与统一授权** | 10 | 事实与版本可复现、证据可引用、统一 credential handle、隔离与 Effect 审计 |
| **Playground 易用性** | 15 | 非工程用户任务成功率、注册到首个验证结果的时间、核心路径独立性、错误可诊断与可停止 |
| **工程质量与净成本** | 10 | 测试可靠性、Token、墙钟、人类分钟、恢复成本，以及相对上一版本或单 Agent 技术基线的净收益 |

### 4.4 等级与平台迭代规则

- **未评级**：P0–P2 任一硬门未通过。
- **60–74：工程原型**：核心契约和部分真实协作已出现，但自动续跑、可比基线或用户路径尚未闭合。
- **75–89：平台 MVP 可用**：硬门全过，DeerFlow 默认 Runtime 稳定，参考负载可重复，非工程用户可独立完成核心路径。
- **90–100：可复用平台**：至少 2 类 Runtime 与 2 个第三方 Agent 无定制接入，连续 3 次平台面板复测稳定，且成本与边界没有退化。

每次平台候选变更都在同一面板下生成评分卡，与上一平台基线比较：**硬门不能退化，平台核心能力不能退化，总分必须提升；否则不进入主线。**`fleet-eval-01` 根据评分定位最弱维度，提出下一轮实验并复测，但不能修改评分规则、验收自己或自动合入代码。面板与阈值版本化，禁止为提高分数而临时改题。无法证明增益的 Harness、角色、工具和步骤优先删除。

### 4.5 开发时怎样调用，怎样避免无限迭代

离线校验（不连接 Fleet、不调用模型）：

```bash
PYTHONPATH=. python scripts/fleet_eval_run.py validate
```

真实授权与单题冒烟完成后，才允许用同一 CLI 的 `run` 或统一 operation `evals.run`（`POST /api/eval/runs`）启动单轮。先用 `mode=single` 生成技术对照，再用 `mode=multi` 引用该基线；`single` 禁止持久化候选报告。真实运行必须记录 `active_human_seconds`，独立 P1／P2 证据只能从测量文件输入。

开发时的循环由维护者控制，一轮只验证一个明确实验：

1. **调用评测**：在 PR、发布候选或人工触发时运行 `fleet-eval-01`，生成评分卡和最弱项证据。
2. **持久化建议**：建议进入 `fleet_eval_recommendations`，初始状态只能是 `proposed`；它不会自动变成开发 Task。
3. **维护者决策**：维护者选择 `accepted` 或 `rejected`。只有被接受的建议才允许安排一次有边界的开发改动。
4. **实现并复测**：完成后由 CI 或维护者再次调用 `fleet-eval-01`，使用同一面板比较基线；建议只能转为 `implemented → verified/closed` 或标记失败，不能自动触发下一轮开发。`verify` 必须携带 `candidate_report_id`，且候选报告满足 VGH 相对可比基线**严格大于**、全部硬门与目标分通过、安全／质量／成本护栏不退化；持平不算 improve。
5. **冻结或继续**：达到当前发布门就冻结范围并发布；只有仍存在高价值、可验证的差距时，才由维护者开启下一轮。

建议状态机为：`proposed → accepted/rejected → implemented → verified/closed`。**没有 `proposed → implemented` 的自动路径，也没有“OUTPUT 一生成就自动重启评测／开发”的路径。**

满足以下任一条件必须停止本轮迭代：

- 当前目标已达到：MVP 阶段是硬门全过、达到 75 分且 VGH 相对可比基线严格改善、护栏不退化，不是无限追求 100 分。
- 连续 2 轮没有净改善，或同一失败假设重复出现。
- 达到本轮预设的时间、Token 或实验次数上限。
- 没有新的、能用证据验证的实验假设，或维护者主动停止。

因此，持续测试产生的是持续证据，不是自动执行的无尽开发任务。

**当前评级：未评级。**真实 `core-v1` 第二份报告为 58 分，P0 自动唤醒闭环已通过；`fleet-eval-01` 已注册、评分与建议已持久化，但专用模型执行、生产持续监控、第三方线上接入复验和非工程用户核心路径验收仍未完成，因此不能按 P0 已通过推定整个平台达标。这与任何第三方 Goal 的业务评分无关。

## 5. 统一协议层：三种调用来源与业务边界

### 5.1 名称、版本与唯一真相源

平台的操作协议是 **`am-fleet-ops` v1.0**，第三方接入面 FAP 使用同一 major 版本 `PROTOCOL_VERSION = "1.0"`。所有响应携带 `X-Protocol-Version`；不兼容变更必须提升 major 版本。`api_version`、`server_version` 和 `deployment_version` 是服务／部署标识，不与协议 major 混用。

[`fleet-ops-protocol.json`](fleet-ops-protocol.json) 是**唯一机器可读操作清单**，登记每个 operation 的稳定 ID、HTTP method、path、surface、读写属性、风险确认和超时。新增 Control Plane API、页面独占操作或执行能力时，必须同步维护清单与协议测试，不能在页面或 Agent 里另藏一套操作语义。

调用方可以自发现：

| 入口 | 用途 |
|---|---|
| `GET /api/meta` | 当前协议版本、部署能力、鉴权矩阵和实际公开 operations |
| `GET /api/ops/protocol` | 完整 `am-fleet-ops` 操作清单 |
| `GET /api/agent-doc` | 面向第三方 Agent 的接入说明 |
| `GET /openapi.json` | 当前 Control Plane 的精确请求／响应 Schema |

### 5.2 三种调用来源，不是三套业务协议

```text
GUI / Playground
  → POST /api/console/operations/execute ─┐
                                          │
内部 fleet-ops-01                         ├→ 同一份 operation 定义与业务 API
  → POST /internal/ops/execute ───────────┤   → Registry / Task / Loop / Inbox
                                          │   → Scheduler / RuntimeAdapter
外部第三方 Agent（PAT）                   │
  → operation 对应的 method + path ───────┘
```

| 调用来源 | 实际调用方式 | 身份与边界 |
|---|---|---|
| **GUI／Playground** | 前端 `executeOperation(operationId, request)` 将 `operation_id + path_params + query + body + confirmed + idempotency_key` 提交到 `POST /api/console/operations/execute`；Console 再按清单解析到实际 operation | 使用员工 OAuth／inhouse 会话；服务端覆盖真实 actor，浏览器不能伪造。不得调用 Runner 专用的 `/internal/ops/execute`，不得直读业务数据库或另造页面特权业务规则 |
| **内部操作助手 `fleet-ops-01`** | Runner 的 `fleet:ops` 工具将同一参数信封提交到 `POST /internal/ops/execute`；内部执行代理按 operation ID 确定 surface、method 和 path | 该入口只接受内部服务调用。系统 PAT 由服务端 Vault 注入 Control Plane 请求，原始 token 不进入 Runner、Thread、YAML 或日志；模型不能任意指定 URL |
| **外部第三方 Agent** | 先读 `/api/meta`、`/api/agent-doc` 和 `/api/ops/protocol`，再按 operation 的 method＋path 调用 Control Plane；写请求携带 `Idempotency-Key`，429 按 `Retry-After` 退避 | 使用 `Authorization: Bearer <PAT>`；读操作需要 read，写操作需要 write，write 隐含 read。不能调用内部执行代理，不能借 Agent 名称绕过 PAT scope、审计或幂等校验 |

**统一协议不等于所有来源都调用同一个 URL。**当前 GUI 和内部 Runner 各有受信任的执行代理，外部第三方调用 operation 的 HTTP 绑定；目前没有面向三类客户端公开的单一 `/api/ops/invoke`。统一的是 operation 契约、业务实体、状态机、权限、幂等、错误和审计语义，而不是传输入口的表面形式。

### 5.3 一个注册 Agent 的完整调用链

协议清单中的操作是：

```text
agents.create → POST /api/agents
body: { "yaml": <完整 agentfleet/v1 文档> }
```

三种来源分别这样到达同一业务操作：

```text
GUI:
  POST /api/console/operations/execute
  {"operation_id":"agents.create","body":{"yaml":{...}},"confirmed":true,"idempotency_key":"gui-..."}

内部操作助手:
  POST /internal/ops/execute
  {"operation_id":"agents.create","body":{"yaml":{...}},"confirmed":true,"idempotency_key":"ops-..."}

外部第三方:
  POST /api/agents
  Authorization: Bearer <write PAT>
  Idempotency-Key: third-party-...
  {"yaml":{...}}
```

前两种信封由各自执行代理解析，最终也到达 `POST /api/agents`。三者共用同一 YAML 校验、Registry 持久化、Runtime 部署、MCP／egress 对账、错误与审计路径。`providers.register` 注册的是运行时能力提供者，不是可认领 Task 的 Agent，不能混用。

### 5.4 业务层与客户端／表现层怎样分离

| 层 | 负责什么 | 不应该做什么 |
|---|---|---|
| **客户端／表现层** | 页面渲染、表单、聊天、进度展示、风险确认、OAuth／PAT 身份接入 | 不复制 Task／Loop 状态机，不直接改业务表，不把按钮或模型回复当作业务成功 |
| **协议层** | 版本化 operation ID、参数信封、HTTP 绑定、能力发现、读写与风险标记 | 不实现业务 Goal，不用自然语言代替权限，不允许未登记 operation 绕过执行器 |
| **Control Plane 业务层** | Agent Registry、Task／Loop、交付验收、Inbox、Scheduler、授权、幂等和审计 | 不依赖某个页面或某个 Agent 的私有表达；不为三类客户端复制三套业务逻辑 |
| **RuntimeAdapter／执行层** | Agent 部署、Thread／Run、恢复引用、模型目录和 capabilities | 不让客户端绕过稳定 surface 依赖 Runtime 私有表或特权接口；不支持的能力显式返回，不能伪装成功 |

业务执行统一使用 **Task／Loop 和强制交付验收**；GUI 的按钮、操作助手的聊天和第三方 API 写入都只是触发方式。高风险操作仍需额外确认，human-only 操作仍由人完成，秘密正文仍只走人工表单或 Vault，协议统一不会降低这些边界。

### 5.5 新开发者必须保持的约定

1. 新能力先登记 operation，再接 GUI、内部 Agent 或第三方；不要先做页面私有 API 再补协议。
2. 三类客户端可以有不同的传输适配器，但不得产生不同的业务结果、权限旁路或错误语义。
3. 外部第三方不得调用 `/internal/ops/execute`；该内部入口使用系统凭证，不能简单公开成统一公网 invoke。
4. 写操作保留确认与幂等；高风险、human-only 和秘密边界不能因 operation ID 而绕过。
5. 协议测试应验证“同一 operation、三种身份通道”的一致性，同时明确当前第三方使用 HTTP operation 绑定，而非虚构一个不存在的公开统一 invoke。

## 工程现状与历史进度

**阶段**：✅ Spike 0001 已通过（2026-10-08，Go）。✅ Fleet Console v0 已可用。✅ [Spike 0002](docs/spikes/0002-adrs0008-door-validation.md) 已通过（2026-10-09）——ADR-0008 开门验证全过。**阶段**：✅ [Spike 0003](docs/spikes/0003-task-pool-acceptance.md) 已通过（2026-10-09 晚，冷启动回归全绿）——ADR-0010 任务池全链路：task/loop 分离、竞争领取 ≤3、强制验收、打回同 thread 续跑、人机同构交付、runner 工具 agent_spawn/agent_message（零改 DeerFlow 源码）。Console 三件套：Fleet Loop 看板 + 交付验收 + task 详情（任何状态可进入/可终止）。

**任务池已落地（ADR-0010，2026-10-09 晚）**：task/loop 分离、竞争领取 ≤3、交付自动捕获、强制验收（采纳/退回/人交付）、打回同 thread 续跑；runner 工具 `agent_spawn`/`agent_message` 经 config.yaml `group: fleet` 挂载（未改 DeerFlow 源码），指挥官可自主派子任务入池。Console 看板已加交付验收（交付比较 + 采纳/退回）。

~~任务池模型已立项（ADR-0010，2026-10-09）~~：task=验收单元 / loop=执行单元，领取三途径（人指派/人自领/指挥官认领 cron），同 task ≤3 个并行 loop，验收强制。随 P3 实施（先于/伴随 agent_spawn）。

**P2 主体已落地（2026-10-09）**：`control-plane/`（Registry + Admin API :8003 + Dispatcher + Inbox + Scheduler 薄层）、`agents/` YAML 仓库（首个 agent：应收对账）、Console 升级（Registry agents 区 + agent 详情页配置/对话一页合并，QA-7）。端到端冒烟全过：YAML 部署→网关同步、任务派发 token 落库、Inbox 攒批合并唤醒、cron 对账建任务。**P3 收尾完成（2026-10-09 夜）**：dispatcher 多消费者并行（3 worker，实测 6s vs 串行 40s+）；cron 池化（croniter 触发 → 池任务 → 默认所属 Agent 认领，显式 `orchestrator` 才交指挥官，ADR-0016）；未认领任务池 GUI（横向货架 + 领取/指派/关闭）。**P3 完整收口（2026-10-09 深夜）**：人机认领统一（单代码路径，人=`human`，根治双路线 bug）；人 loop 不计 3-loop 上限 + 人交付框（贴飞书链接进交付验收，与 agent 产物同构）；统一认领 UI（🧑/🤖 单一下拉）；URL 自动链接化；会话详情页就地验收；四标签筛选视图（待分配/执行中/待确认/已验收）；批量清理 30 个滞留任务；scheduler sync 返回模型修复；冷启动回归全绿。

**P3b 已落地（2026-10-10）**：FAP v1 第三方 agent 接入面（[ADR-0011](docs/adr/0011-third-party-agent-access-protocol.md)，协议清单 [agent-api.md](docs/design/agent-api.md)）——`GET /api/meta` 免鉴权自发现 + `GET /api/agent-doc` 机器可读文档 + 全响应 `X-Protocol-Version`；写操作 `Idempotency-Key` 幂等（`fap_idempotency_keys` 表 24h 窗口，同键同 actor 回放、跨 actor 409）；per-PAT/IP 令牌桶限速（`429 + retry_after`，60/min 可配）；写操作 `source`/`actor` 自报字段透传 `audit_events.detail`；PAT 鉴权（复用 DeerFlow 原生 `personal_access_tokens` 同库表，scope 仅 read/write，控制面自校验；Console 新增 SETTINGS 页签发/吊销）。参考客户端 `examples/fap-quickstart.mjs`（零依赖 Node≥22，自发现→发起→领取→交付→轮询验收全链路实测通过，含幂等回放 demo）。**边界**：FAP 端点仅内网绑定，公网暴露待 P4 OIDC/PAT 全链路（ADR-0011 第 5 条）；loopback 无 PAT 放行仅限 Console 本机代理。

**Fleet 全量操作协议（ADR-0017）**：[`fleet-ops-protocol.json`](fleet-ops-protocol.json) 是唯一机器可读操作清单，`GET /api/ops/protocol` 自发现全部操作；默认操作 Agent `fleet-ops-01` 通过专属 `fleet:ops` 工具组按 operation ID 执行，读操作可直接执行；普通写操作直接发送并保留幂等校验，高风险操作需额外确认且确认后立即关闭弹窗（ADR-0033）。PAT 签发、产物下载和非空秘密配置保持人工操作。首页右下角浮窗提供该 Agent 的持久自然语言会话，不为每次聊天创建业务 Task。激活与统一验收：

```bash
# 服务启动后：幂等注册并做真实只读调用
console/backend/.venv/bin/python scripts/fleet_ops_activate.py
# 全量验收；--offline 可只跑确定性测试
bash scripts/test_fleet_ops_all.sh
```

74 项在线矩阵报告位于 `artifacts/fleet-ops-acceptance.json`。系统 Agent 配置只读，本地与第三方统一 PAT 鉴权，系统凭证只支持人工轮换（ADR-0018）。详见[协议设计](docs/design/fleet-operations-protocol.md)。

**协议化与 RuntimeAdapter（ADR-0031/0032/0033/0034，2026-11-01）**：旧 Console、Playground、操作助手与第三方 Agent 统一按 operation ID 读写；普通写操作直接发送并保留幂等校验，高风险操作需额外确认且确认后立即关闭弹窗，人类输入立即上屏且可连续发送，human-only 秘密仍只走人工表单。Thread／Run 由 Control Plane 的稳定 Runtime surface 提供，Console 不再直读 DeerFlow 表。`FLEET_RUNTIME` 默认 `deerflow`；`deepseek-thin` 为 OpenAI 兼容测试参考实现，支持注册、执行、消息与 requeue，不支持 Checkpoint resume、SSE、反馈、产物或 subagent 工具编排。`GET /api/runtime/capabilities` 自发现能力；冷切换与回滚只读预检见 `scripts/runtime_cold_switch_check.py` 和 `deploy/DEPLOY.md` §8，生产未切换。

**工程遗留（不改变当前第一切片）**：fork DeerFlow 锁版本与 P2 规模化验收已完成；后续包括阿里云首次部署实测（ECS 购买后按 DEPLOY.md §4）、P4（OIDC＋成本台账）。标准平台负载的自动唤醒闭环、可比技术基线与非工程用户路径按第 3 节优先推进。

## Fleet Console（`console/`）

管理界面 MVP，按「Agent 定义」与「任务执行实例」两条线组织：

- **Playground（工作主页）**：默认路径 `/`（`/playground` 保留别名），用于 Agent 对话、场景观察与 Operations。普通 Agent 的聊天入口选择或创建 Task，并与 Task Detail 共用执行 Thread；Fleet Ops 在 Playground 与其他页面共用持久操作 Thread。
- **Task Board（任务看板）**：路径 `/tasks`，发起 task instance，查看父子任务树、领取状态与交付验收；实例层级统一为 task → loop → thread。旧 `/fleet` 仅作兼容重定向，不再与 Fleet tab／Fleet Board 混称。
- **Registry / Agent 详情**：管理 Agent Definition（YAML、配置版本、停止/拉起/销毁）；关联 task instances 只是按定义筛选的横向引用，不属于实例 breadcrumb。
- **Thread 详情与运行观测**：查看消息流、Runs、Checkpoints、产物和 Token；从 loop 进入工作会话，退回修正复用同一 thread。
- 架构：`console/backend`（FastAPI:8002，asyncpg 只读 PG + httpx 转发网关）+ `console/frontend`（React+Vite）。
- 启动：在 `console/` 或 `console/frontend/` 执行 `npm run dev` 均走完整环境（`scripts/dev.sh` 依次拉起 PG→网关:8001→Console 后端:8002→Control Plane:8003→前端，退出全回收）；仅调试 Vite 时在 `console/frontend/` 执行 `npm run dev:vite`。Kimi 预览项目根目录为 `console/frontend/`，CLI host/port 参数会透传给 Vite。
- 启动排障：统一入口日志为 `console/logs/dev.log`，各服务日志也在该目录。**没有本次启动记录 = 预览启动器未调用项目脚本**，从最新预览卡片重新启动；有记录则按最后一条失败阶段查对应服务日志。`7100/8001/8002/8003` 可用 `lsof -nP -iTCP:<port> -sTCP:LISTEN` 逐个检查；不要用模糊 `pkill -f` 清场。修改控制面后必须重启 8003，Vite 热更新不会更新 Python 进程。全页同时 `Error: null` 时运行 `cd console && npm run check:stack`，详见[本地预览排障](docs/dev/local-preview-troubleshooting.md)。
- 本机网络：本地健康检查强制 `curl --noproxy '*'`；Console 本机转发使用 `httpx.AsyncClient(trust_env=False)`，避免 shell 代理配置导致 500 / Failed to fetch。不要通过放宽代理或跳过鉴权来掩盖连接问题。
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
| [docs/design/shared-pool-concurrency.md](docs/design/shared-pool-concurrency.md) | 共享池设计：多用户并发 / know-how 共享 / task output 复用（QA-13~24 决议，ADR-0012/0013/0014） |
| [docs/dev/p3b-fap-kickoff.md](docs/dev/p3b-fap-kickoff.md) | **P3b 开工说明（给 coder）**：FAP 接入面任务分解、现状资产地图、验收标准 |
| [docs/dev/p2-scale-acceptance.md](docs/dev/p2-scale-acceptance.md) | **P2 规模化验收测试计划**：10 agent 注册、cron 准时、休眠/唤醒全链路 |
| [evals/platform/](evals/platform/) | 平台版本化评测场景、受控事实、确定性评分器与有界建议状态机 |
| [AGENTS.md](AGENTS.md) | 仓库约定：目录结构、安全红线、开发规范（人类与 AI 编码助手共用） |
| [deploy/DEPLOY.md](deploy/DEPLOY.md) | **部署手顺**（运维入口）：ACK 测试发布、ECS Compose 生产部署、验收 checklist、安全红线 |
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
| [0012](docs/adr/0012-control-plane-atomicity-singleton.md) | 控制面并发原子性与单例门闩（认领事务化 / 认领者唯一 / 幂等占位行 / advisory lock） | 已接受 |
| [0013](docs/adr/0013-knowhow-lineage-memory.md) | know-how 谱系与记忆治理（lineage / facts 维持共享 / PAT 绑定员工） | 已接受 |
| [0014](docs/adr/0014-answer-library-task-search.md) | 已验收答案库与 task_search（引用型交付 / 无失效概念） | 已接受 |
| [0015](docs/adr/0015-universal-human-agent-acceptance.md) | 人与 Agent 同权验收（含 FAP/A2A + PAT 第三方 Agent） | 已接受 |
| [0016](docs/adr/0016-cron-default-owner-agent.md) | cron 默认直派所属 Agent，显式配置才交编排者 | 已接受 |
| [0017](docs/adr/0017-fleet-operations-protocol.md) | Fleet 全量操作协议与默认操作 Agent | 已接受 |
| [0018](docs/adr/0018-system-fleet-ops-and-pat.md) | 系统内置 Fleet 操作 Agent 与统一 PAT 鉴权 | 已接受 |
| [0031](docs/adr/0031-unified-protocol-playground-and-task-conversation.md) | 统一协议客户端、Task 群聊投影与可替换 Playground 表现层 | 已接受 |
| [0032](docs/adr/0032-runtime-adapter-and-cold-switch.md) | RuntimeAdapter 边界与整站冷切换 | 已接受 |
| [0033](docs/adr/0033-risk-based-operation-confirmation.md) | 风险分级确认与确认后立即释放界面 | 已接受 |
| [0034](docs/adr/0034-optimistic-chat-and-async-agent-runs.md) | 人类消息乐观上屏与异步 Agent 执行 | 已接受 |
| [0035](docs/adr/0035-platform-self-evaluation-agent.md) | 统一平台评测 Agent、确定性评分与有界自迭代 | 已接受 |
| [0036](docs/adr/0036-child-completion-resume-credentials-and-budgets.md) | 子任务完成续跑、共享凭据句柄与执行预算（凭据句柄部分已被 ADR-0037 取代） | 已接受 |
| [0037](docs/adr/0037-reuse-yaml-timeline-access.md) | Timeline 访问复用现有 YAML 能力，否决专用代理层 | 已接受 |
| [0038](docs/adr/0038-thirdparty-execution-access-semantics.md) | 第三方执行接入：单 PAT、结构化终态、批量读取、确认取消与真实能力发现 | 已接受 |
| [0039](docs/adr/0039-ops-bounded-task-wait.md) | Ops 长任务的有界服务端等待，消除模型内紧密轮询 | 已接受 |
| [0040](docs/adr/0040-console-inhouse-only.md) | Console 仅允许 `role=inhouse`，FAP/PAT 通道保持不变（第 1、4 条已被 ADR-0041 取代） | 已接受 |
| [0041](docs/adr/0041-console-inhouse-group-check.md) | Console inhouse 判定改为 coin 分组查询（`ids` 含 2），失败 fail-closed | 已接受 |
| [0042](docs/adr/0042-runtime-capability-plane-awareness-and-orchestration.md) | 运行时 Capability Plane、Agent 感知、共享工作空间与自适应编排 | 已接受 |
| [0043](docs/adr/0043-effective-agent-config-reconciliation.md) | 版本化 Agent 全量配置对账与 inherit-all 重置 | 已接受 |
| [0044](docs/adr/0044-vgh-and-bounded-manual-evaluation-runner.md) | VGH 北极星、单 Agent 对照与有界手动评测启动器 | 已接受 |
| [0050](docs/adr/0050-loop-waiting-and-final-artifact-delivery.md) | Loop 等待子任务与最终 Artifact 交付分离 | 已接受 |

## 关键输入（已确认的 QA 结论）

- 并发峰值约 10 个 agent；注册规模目标数千。
- DeepSeek 官方 API key 已就位；不自建 GPU。
- 触发方式：Web 对话（公网，出差可用）/ IM / 定时 / agent 唤醒 agent。
- 授权：业务系统 OAuth，普通 agent 用员工授权（OBO），超级 agent 用 M2M 服务账号。
- 弱人工审批需求；纯内部使用，无等保/出境约束。

## 历史阶段回顾

~~第一步（本周，1 人 3–5 天）~~：✅ [Spike 0001](docs/spikes/0001-deerflow-basic-lifecycle.md) 已完成（Go）；✅ Fleet Console v0 已可用（原 P4 内容提前，见上节）。

**P2 阶段（主体已落地 2026-10-09）**：~~前置任务 fork DeerFlow 仓库锁版本（QA-8）~~ ✅（[khonsou/deer-flow](https://github.com/khonsou/deer-flow) tag 锁定；当前部署基线 `am-fleet-base-v2`）；~~ADR-0008 开门验证~~ ✅（[Spike 0002](docs/spikes/0002-adrs0008-door-validation.md)）；Agent Registry（组织级包装层）+ Admin API + YAML 版本化 ✅；Dispatcher + 队列 + Inbox ✅（队列 PG 实现，Redis Stream 为部署期替换）；Scheduler(cron) ✅（薄层对账，不自建触发引擎）；Console agent 详情页"配置 + 对话"一页 ✅（QA-7）。~~P2 规模化验收~~ ✅（2026-10-10，[docs/dev/p2-scale-acceptance.md](docs/dev/p2-scale-acceptance.md)：10 agent 批量注册生命周期、cron 节奏 120.15s±、休眠/唤醒全链路，脚本 `scripts/p2-scale-acceptance.py` 可重放；途中修复休眠唤醒穿透与销毁 FK 两个 bug）。**剩余**：§12 第 1–4 条 QA（IM 选型、IdP、M2M 清单、首批 agent 清单，分别阻塞 P3/P4）。详见设计文档 §9。
