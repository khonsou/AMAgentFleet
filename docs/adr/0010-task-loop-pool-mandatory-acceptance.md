# ADR-0010：任务池模型 —— task（验收单元）/ loop（执行单元）分离，竞争领取，强制验收

- **状态**：已接受
- **日期**：2026-10-09

## 背景

ADR-0004 定义了协作语义（线程树 + 摘要返回），但落地过程中暴露两个缺口：

1. **派发是直通管道不是池**：任务创建即被消费，无 backlog、无领取、无"等人/agent 来领"的状态（2026-10-09 Console 实测协作流程时确认）。
2. **没有验收位**：任务只有 success/error 终态。QA 结论 5"弱人工审批"被实现成了"没有人的位置"——但**验收 ≠ 事前审批**：砍掉的是审批台，不是质量门。

生态调研（Hermes Kanban、agentboard 等，2026-10-09）确认行业范式是 durable work queue：任务板是持久工作队列而非 RPC，dispatcher 原子认领，人机通过同一任务面协作。

## 决定

1. **task 与 loop 分离**：
   - **task = 验收单元**（open → review → done）。人创建；可由指挥官分解为 sub-task（编排嵌套）。task 状态只前进不回退。
   - **loop = 执行单元**（claimed → running → delivered → returned → running →… → accepted/archived）。一次领取 = 一个 loop = 一条 thread。打回只作用于该 loop（同 thread 水合续跑修正），不影响 task 与其他 loop。
2. **领取与竞争**：task 开放领取，agent 或人均可；同一 task 最多 **3 个并行 agent loop**（验收不采纳即自然终止，上限防无限烧 token）；**human loop 不计入上限**（不消耗平台 token，2026-10-09 修正）。领取**非独占**——不同 agent/人的产出在验收时并排比较。
3. **强制验收**：交付自动捕获（摘要 + 文件链接 + thread 引用），任一 loop 交付即 task 入 review；**必须**经验收动作（采纳 → done / 保持 open 等更多 loop / 关闭作废）才算终结，无一豁免。验收人 = task 创建者。采纳记审计。
4. **任务来源三途径，无暗路径**：① 人在页面指派给具体 agent；② 人自己领取；③ cron 计时启动的**舰队指挥官认领**（指挥官是常驻角色，承接所有定时任务）。agent 不自取任务。
5. **人机同构**：人领取 task 后在 Console 对话交付，产出结构与 agent loop 完全一致；验收比较时不区分人机来源。

## 后果

- 正面：每一层都有质量闸（指挥官验 sub-task、人验根任务）；并行比较天然支持"不同解题者同台"；cron/IM/agent 触发统一入池，削峰且可干预；打回利用 checkpoint 续跑，修正成本低。
- 负面：任务终结多一步验收操作（强制）；3 loop 上限下竞争失败的算力浪费被明确接受（换取不停锁与可比较性）。
- 数据模型增量：`fleet_task_loops`（loop 表：task_id/claimant_type/claimant/thread_id/deliverable/status/version）、`fleet_task_comments`（验收意见，人机共用）；`fleet_tasks` 状态收敛为 open|review|done，加 accepted_loop_id。
- 实施序列：列入 P3，先于/伴随 `agent_spawn` 工具（指挥官派 sub-task 入池即 tool 的实现）。

## 已考虑的备选

- **原子独占认领**（Hermes 式）：省 token 但锁死单解、无比较可能，且 crash 回收复杂——否决。
- **done 即终态、验收可选**：与"验收必须强制"的 QA 决定冲突——否决。
- **打回使 task 整体回退**：回退粒度应属于执行层而非验收层；task 只前进——否决。
