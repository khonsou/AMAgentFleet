# 编排完成等待契约修复记录

日期：2026-11-05

## 平台 bug 与归因

多 Agent 根任务首次 `delivered` 可能只是派单说明。外部客户端只能等待固定秒数或猜测子任务是否结束，无法确定“直接子任务已终态、父任务已自动唤醒并再次交付”的时刻。

这属于控制面等待与执行状态投影问题，不属于业务内容、业务 Agent 或 grader 优化。

## 测试先行与红色证据

在 `AMAgentFleetTestcase` 新增 `cases/platform/orchestration-wait.yaml`，要求两个真实子任务、系统自动唤醒、最终拼接 OUTPUT、零人工干预，并增加 `orchestration-complete` grader。

- 未修复平台的 `WAIT_TARGETS` 不含 `orchestration_complete`，目标请求被拒绝；专项单元测试先红。
- run `20261105T053638Z-orchestration-wait-t1-f26ecee9` 中，`orchestration-complete` 为 `inconclusive`，没有可用的完成证据。
- 修复等待语义后，run `20261105T073510Z-orchestration-wait-late-f26ecee9` 已能返回完成条件，但 worker B 因 `stop_reason=length` 没有真实 OUTPUT，整个 case 仍正确失败：`parts-match` 与 `joined-matches` 为 `fail`。这证明“编排完成”不会被误当成“业务验收成功”。

最后一项失败归因于测试用例复用了长文 worker。为隔离变量，只给该 case 固定受控 echo worker setup，不修改 grader、长文交付 case 或业务 Agent。

## 最小平台修复

- `tasks.wait target=orchestration_complete`：只有直接子任务存在且全部终态、根任务已交付、自动唤醒后再次交付时才返回 `condition_met`。
- 返回 `orchestration` 状态：`waiting_children`、`children_terminal`、`root_delivered`、`final_delivery_version`、`wake_count`。
- 空子任务集合与首次派单交付不得通过完成判定。
- `task_detail.children` 使用最新 Loop 投影执行状态，避免 Loop 已永久失败而 Task 仍显示 `queued/open`。
- 失败子任务同样触发父任务恢复检查；存在重试恢复动作时不视为永久终态。
- 更新 `/api/agent-doc` 与 `fleet-ops-protocol.json`，明确编排完成不等于业务验收通过。

## 最终真实复验

run：`20261105T074135Z-orchestration-wait-t1-285c9f16`

- 根 Task 787，根 Loop 804 最终 `version=2`，对应一次子任务完成后的自动唤醒交付。
- 子 Task 788／789 均为 `execution_status=succeeded`，分别返回两个受控标识。
- `system:child-completion` 评论包含两个真实子任务 OUTPUT。
- `orchestration_complete=true`，`auto_wake=true`，`human_interventions=0`。
- 最终 OUTPUT 为合法 JSON，`parts` 与 `joined` 均精确匹配。
- 总体结果：`pass`，7／7 graders 通过，0 fail、0 inconclusive；VDT 25.759198 秒。

## 全量回归

- 控制面：264 项测试通过，21 项按条件跳过。
- 测试项目：4 项单元测试通过。
- 平台套件校验：`long-delivery-resume` 与 `orchestration-wait` 均通过配置校验。
- 本次最终复验使用 `--k 1`，不宣称多次重复稳定性已经验证。

## 保留边界

- 不修改业务 Agent、业务 Soul、业务 Skill 或 grader。
- 不自动 push、merge 或 deploy。
- 长产物跨线程 artifact、分段持久化和显式截断字段仍属 P-003／P-001／P-002 后续范围。
