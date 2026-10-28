# 共享任务池：多用户并发 / know-how 共享 / task output 复用 —— 设计说明

> **状态**：已决议（QA-13 ~ QA-24，2026-10-15）
> **关联**：ADR-0010（任务池）· ADR-0011（FAP 接入面）·
> [ADR-0012](../adr/0012-control-plane-atomicity-singleton.md)（并发原子性与单例门闩）·
> [ADR-0013](../adr/0013-knowhow-lineage-memory.md)（know-how 谱系与记忆治理）·
> [ADR-0014](../adr/0014-answer-library-task-search.md)（已验收答案库与 task_search）·
> [ADR-0015](../adr/0015-universal-human-agent-acceptance.md)（人与 Agent 同权验收）
> 上位设计：[../../deepseek-agent-fleet-mvp-design.md](../../deepseek-agent-fleet-mvp-design.md) §7

---

## 1. 定位与北极星

系统本质 = **任务市场 + know-how 固化机 + 已验收答案库**。

- **任务市场**：全公司唯一共享任务池（QA-5/QA-9，无部门/个人隔离）；
  人（Console）与人的 Codex（FAP/A2A，PAT 认证）同入口发起；人与 agent
  同构认领、同构交付、同构被验收。
- **know-how 固化机**：一类任务被人重复做好几次后，员工注册专精 agent
  固化方法（A 建）；其他员工或他们的 Codex 可直接复用（B 用）。
- **已验收答案库**：task 交付物持久化且验收后不可变，经 task_search
  检索复用——做过了就不再重做。

**北极星十六字：执行隔离、方法共享、结果走池、一切归因。**

| 原则 | 语义 |
|------|------|
| 执行隔离 | 一次认领 = 一个 loop = 一条 thread；上下文 / checkpoint / 工作目录按 thread 隔离；同 agent 并发实例互不可见 |
| 方法共享 | agent 定义、skills、deermem facts 全员共享复用（know-how 固化通道） |
| 结果走池 | task 结果以"已验收交付"沉淀在池内；复用经 task_search + 验收确认，不走 agent 私有记忆 |
| 一切归因 | 人（OAuth）/ 人的 Codex（PAT 绑定员工）/ agent（继承发起链），每个动作可归因 |

## 2. 设计目标（Why）

| # | 目标 | 为什么 |
|---|------|--------|
| G1 | **单一池、全透明** | 小团队养不起部门墙；透明取代审批成为治理手段（看得见 = 可追责 = 弱审批成立的前提） |
| G2 | **人机同构 PK** | 不预设 agent 或人谁更好；同一 task 多份产出并排比质量，采纳率成为 agent 的客观考核 |
| G3 | **know-how 双通道固化** | 公司最有价值的资产是"会做某事"的方法：通道一 = 注册 agent（方法进 YAML/soul/skills），通道二 = facts/skills 自动沉淀；复用零损耗传播 |
| G4 | **一切可归因** | 透明池里每个动作必须能回答"谁干的"；没有归因，透明就只是围观 |
| G5 | **隔离与复用边界清晰** | 多并发下只有边界清晰才不出事故：执行隔离（thread 级）、方法共享（agent 级）、结果不缓存（池 + 源系统为准） |

## 3. 状态作用域 × 并发语义总表

任何并发问题用这张表做基准尺（现状差距见 ADR-0012 的修复清单）：

| 状态层 | 作用域 | 并发语义（目标态） |
|--------|--------|--------------------|
| agent YAML / 注册行 | 全局共享，版本化 | 并发部署原子化、同名串行；运行中 loop 用起跑时物化的配置 |
| task / loop（池） | 全局共享 | 认领事务化；同 task 终身累计 ≤3 agent loop（QA-13）；`(task_id, claimant)` 唯一（QA-14）；任何已识别的人或 Agent 均可采纳/退回（ADR-0015 取代 QA-15 权限） |
| thread / checkpoint | **loop 私有** | 同 thread 并发 run 由网关 ConflictError 拒绝；超时回收有心跳（ADR-0012-7） |
| 交付 deliverable | 全局可见，**验收后不可变** | 复用只引用；并发读天然安全 |
| deermem facts | **per-agent 跨任务共享** | 跨时间接力（先完成的提炼、后启动的注入），非实时共享；竞争场景不隔离（QA-18） |
| skills/custom | 全局共享 | 沉淀即生效、全员可见；静态扫描 + moderation 双闸（QA-19） |
| MCP 凭据 / 出口白名单 | server 级共享 | 现状静态 secrets；OBO per-员工令牌为 P3/P4 目标（ADR-0005） |
| 审计 audit_events | 全局追加 | actor 权威化：JWT/PAT 推导，不自报（QA-24，依赖 P4） |
| 控制面后台循环 | **单例** | PG advisory lock 门闩；滚动更新/误双开不再双触发（QA-16） |

## 4. 身份模型（三层）

| 层 | 机制 | 归因 |
|----|------|------|
| 员工（人） | DAO OAuth → RS256 JWT → Console `DaoAuthMiddleware` | JWT `user_id` 为权威 actor（P4 开关 `FLEET_AUTH_ENABLED`） |
| 员工的 Codex / 第三方 agent | FAP PAT（DeerFlow `personal_access_tokens` 表） | **PAT 签发强制绑定员工**（QA-24）；审计以映射后员工身份为准；实施依赖遗留 QA-2（IdP） |
| agent 实例 | 网关单服务身份（default 桶）+ YAML 配置版本 | 继承发起链：task.created_by → loop.claimant → run |

## 5. 用例 → 机制映射

| 用例 | 机制 | 决议/缺口修复 |
|------|------|----------------|
| UC1 并发发起（人 / Codex A2A） | 统一入池（Console API = FAP 同一能力面）；幂等键 | FAP 幂等改占位行（ADR-0012-5）；创建时相似任务提示（ADR-0014-5） |
| UC2 并发认领与 PK | task/loop 分离；同 task ≤3 竞争；人机同构；强制验收 | 认领事务化 + 认领者唯一；任何已识别的人或 Agent 同权采纳/退回（ADR-0012-1/2、ADR-0015） |
| UC3 know-how 固化（A 注册 agent） | YAML/模板/FAP `agents_registry` 注册，全员可见 | lineage 谱系字段 `derived_from_tasks`（ADR-0013-1）；注册并发原子化（ADR-0012-9） |
| UC4 复用（B 用 A 的 agent） | 全员可指派/认领 | 复用度量：被领取次数/采纳率（ADR-0013-6） |
| UC5 执行隔离（同 agent 多 loop） | 一次认领一条 thread；checkpoint/工作区隔离；网关 thread busy 兜底 | 记忆维持 per-agent 共享不隔离（QA-18）；reaper 心跳修复（ADR-0012-7） |
| UC6 output 复用（答案库） | task_search 只读检索；引用型交付；已识别的人或 Agent 验收 | ADR-0014 全文（QA-20/21/22/23）+ ADR-0015 |

## 6. output 复用四规则（ADR-0014 的操作面）

1. **分级**：搜索结果已验收优先、状态必标；只有已验收的叫"公司答案"。
2. **时效询问**：命中后 agent 必须问用户"该交付日期的 output 直接用，
   还是重新取数跑最新"（交付日期即 `delivered_at`，不加结构化字段）。
3. **引用式复用**：agent 给出 task #N 超链接 + "与 #N 相同"即为当前
   task 的有效 output；任何已识别的人或 Agent 均可确认并验收（ADR-0015）。
4. **无失效概念**：output 之间无有效/失效关系；多候选全部列出，
   由验收者判断哪个对当前 task 有效，判断即验收。

## 7. QA 决议摘要（2026-10-15）

| # | 决议 | 承载 |
|---|------|------|
| QA-13 | 认领上限维持现状：同 task **终身累计** ≤3 agent loop；ADR-0010"并行"措辞以 ADR-0012 澄清为准 | ADR-0012 |
| QA-14 | 同一认领者对同一 task 终身一个 loop（唯一约束） | ADR-0012-2 |
| QA-15 | ~~仅任务发起者和 admin 可验收/打回~~（历史决议）；现行为任何已识别的人或 Agent 同权 | ADR-0015 取代 ADR-0012-8 权限 |
| QA-16 | 控制面单例 + PG advisory lock 门闩 | ADR-0012-6 |
| QA-17 | agent 注册记录 lineage（`derived_from_tasks`，可选） | ADR-0013-1 |
| QA-18 | deermem 维持 per-agent 共享，不做竞争隔离；串供提升质量即可接受 | ADR-0013-2 |
| QA-19 | 技能沉淀注册即全局可见，不设晋升评审 | ADR-0013-4 |
| QA-20 | task_search 全量可搜、已验收优先、状态必标 | ADR-0014-2 |
| QA-21 | 不加结构化 as-of 字段；命中即问用户"直接用还是重跑" | ADR-0014-3 |
| QA-22 | 引用型交付 = 一等交付；超链接 + 已识别的人或 Agent 认可即验收 | ADR-0014-3 + ADR-0015 |
| QA-23 | output 无失效概念；多候选列出、验收者判断即验收 | ADR-0014-4 + ADR-0015 |
| QA-24 | PAT 强制绑定员工身份（依赖遗留 QA-2 IdP 落地） | ADR-0013-5 |

## 8. 实施序列（P3c 提案）

1. ADR-0012 原子性修复包（认领事务化 / 唯一约束 / cron 单事务 / inbox
   搁浅修复 / FAP 占位行 / 门闩 / 心跳 / 状态守卫）——**先于 FAP 公网暴露**；
2. ADR-0014 task_search 执行时入口（控制面 search 端点 + fleet 工具）+
   引用型交付的 soul 约定；
3. ADR-0013 lineage 字段 + Console 注册页提示；
4. 创建时相似任务提示（复用 search 端点）；
5. QA-24 PAT 绑定员工 与复用度量，随 P4（OIDC/成本台账）落地。
