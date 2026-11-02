# ADR-0039：Ops 长任务的有界服务端等待

- **状态**：已接受
- **日期**：2026-11-02

## 背景

第三方 Agent 用一次自然语言请求让 `fleet-ops-01` 创建 Task、跟踪交付并完成验收时，业务 Loop 由 Dispatcher 异步执行，而 Ops 只有即时快照 `tasks.get`。三个真实 Case 的业务链路均通过，但 Ops 都需要一次人工“继续”：一个 Case 因重复相同工具调用触发 `FORCED STOP`，另外两个在业务 Loop 仍运行时提前结束当前 Run。Ops Run 结束后没有等待者或事件唤醒机制。

这证明缺口位于“异步 Task 状态变化 → Ops 等待”的契约，不是 Task／Loop 状态机，也不是父 Loop 自动续跑机制。单纯提高循环检测阈值只会增加无效轮询、Token 和限速压力。

## 决定

1. **新增只读 `tasks.wait`**：`GET /api/tasks/{task_id}/wait` 在 Control Plane 服务端有界等待，不在模型回合内轮询。操作登记进 Fleet Operations Protocol，PAT `read` scope 可调用，协议 major 不变。
2. **等待条件显式化**：
   - `delivered`：任一指定 Loop 已 delivered／accepted；
   - `all_delivered`：指定 Loop 全部 delivered／accepted；
   - `terminal`：Task 已终结，或指定执行全部进入结构化终态；
   - `version_gt`：指定 Loop 的版本大于给定值且已再次 delivered／accepted。仅 returned／running 不构成条件满足。
   `loop_ids` 可限定等待对象，缺省使用当前 Task 的全部 Loop。
3. **失败与超时不是成功**：Task／Loop 的结构化永久失败返回 `wait_status=failed`；超过单次 deadline 返回 `wait_status=timeout` 和最后快照。只有 `condition_met` 才能进入后续比较或验收，调用方不得把 timeout 当成交付。
4. **第一版有界实现**：单次 `timeout_seconds` 为 0–120 秒，服务端每次检查后立即返回已满足条件，否则按至多 1 秒间隔读取 PG；响应复用 `tasks.get` 的 Task／Loop／comments／children 并附加 `wait_status`、`reason`。本批不新增消息总线、持久 watcher 或自动恢复 Ops Thread。
5. **Ops 禁止模型轮询**：创建、领取、打回后需要等待时使用 `tasks.wait`；`tasks.get` 只用于初始读取和条件满足后的最终核验。多 Loop 用 `all_delivered`，打回及父 Loop 自动续跑用 `version_gt`。
6. **保留运行时安全门闩**：不调整 DeerFlow 的重复调用警告／硬停止阈值；真正的工具死循环仍应被拦截。
7. **参数错误独立修复**：Ops 调用 `tasks.accept` 必须显式携带 `path_params.task_id`，422 不得被记为业务验收失败或长等待成功。

## 后果

- 第三方原始提示词只需发送一次；等待期间没有模型侧紧密 `tasks.get` 循环，业务交付后 Ops 可在同一 Run 中继续比较和验收。
- 服务端短间隔 PG 检查仍有数据库读取，但次数与模型 Token 无关，且有单次硬 deadline；未来可在不改 operation 契约的情况下替换为事件通知。
- 超过单次 120 秒的任务会显式返回 timeout。当前切片解决已复现的分钟级等待，不宣称实现无限时长持久订阅。

## 验收

- 原始三个 Case 的业务指令与验收标准不变，每个只发送一次用户消息，无人工“继续”；重复运行时仅为全部写操作分配独立幂等命名空间，避免历史键回放。全部完成预期 OUTPUT、Task done 和正确 Loop 采纳。
- Case 1 打回后同 Thread、version=2；Case 2 两个 Loop 都交付后只采纳一个；Case 3 保留 `system:child-completion`，父 Loop version≥2 后才验收。
- 不出现 `FORCED STOP`、429、重复 Task／Loop 或人工冒充父 Loop 自动唤醒；`tasks.accept` 同时携带 path `task_id` 与 body `loop_id`。
- timeout、failed、terminal、all_delivered、version_gt 均有确定性专项测试；既有 Control Plane 与 Ops 协议回归不降级。

## 实施证据（2026-11-02）

- 确定性回归：Control Plane 189 项通过，21 项按既有 PG 条件跳过；新增 `test_task_wait.py` 10 项覆盖等待条件、失败、超时、参数解析和 Ops 规则。
- 最终严格重复性回归：连续 3 轮、共 9 次 Case 全部达到预期终态，且每条只发送一次用户消息；业务断言与 Ops 调用质量断言为 9/9。
- Ops 原始调用审计：`tasks.wait` 均携带真实 path task_id、query 条件和 0–120 秒上限；写操作幂等键均有对应运行的独立前缀；无工具拒绝、无 4xx、无未登记 operation、无 `FORCED STOP`。
- 完整 INPUT／OUTPUT、Task 快照和单轮断言：`artifacts/ops-mainline-cases-20261102/wait-regression-v3/result.json`；三轮动态 ID 审计：`artifacts/ops-mainline-cases-20261102/repeatability-result.json`。
- 范围外观察：一次 Commander 内部 `model_profile=default` 误传经 422／409 后自愈，未产生重复子任务；该事项不计入 Ops 调用质量，也不扩大本 ADR 的模型选择改造范围。

## 已考虑的备选

- **提高 loop_detection 阈值**：否决；掩盖症状并削弱死循环保护。
- **在提示词里要求更多轮询或人工继续**：否决；根因仍在，且增加调用方负担。
- **第一版就实现持久 watcher／事件总线**：暂缓；当前有界等待已覆盖真实失败场景，避免扩大架构面。需要跨分钟、跨进程订阅时再按独立 ADR 增加。
