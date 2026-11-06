# ADR-0050：Loop 等待子任务与最终 Artifact 交付分离

- **状态**：已接受
- **日期**：2026-11-06
- **补充**：细化 ADR-0010 的 Task／Loop 交付语义与 ADR-0036 的父子唤醒；ADR-0038 的内部 Loop 状态机保持不变。

## 背景

冷启动 task 823／loop 840 中，指挥官已写合并稿并派发 reviewer #829，明确要等待审阅后再交付。DeerFlow 按 Run 检查 outputs 变化，要求当前 Run 调用 `present_files`，否则返回 `Artifact delivery incomplete`；Control Plane 将该错误默认为 permanent，父 Loop 在 reviewer 交付前失败，后续唤醒又只接受 delivered 父 Loop。单纯要求中间 Run 立即 present 会让未审阅稿件提前进入验收。Run 级 artifact 完整性与 Loop 级多阶段编排因此冲突。

## 决定

1. **Run 结束不等于 Loop 最终交付**。Dispatcher 在每次 Run 后检查直接子 Task；仍有未终态子 Task 时，Loop 进入等待，不进入 Task review，不记 permanent failure。该规则同时适用于 Runtime 成功返回和上述 artifact incomplete 错误。
2. **等待是附加投影，不新增 Loop 状态**。`fleet_task_loops` 增加可空 `waiting_for_children`：`true` 表示等待子任务，`false` 表示最终交付，`NULL` 兼容既有记录。Loop 仍可为 delivered，以便复用现有 `return_loop` 和同 Thread 唤醒；Task 保持 open，不表示执行成功或可验收。
3. **最终交付门不放宽**。直接子 Task 全部终态后，等待中的父 Loop 增加 version 并沿原 Thread 续跑；最终 `deliver_loop` 清除等待标记。最终 Run 写了 outputs 却未 present 仍按 Runtime 错误失败，普通 DeerFlow Run 的 fail-closed 规则不改。
4. **外部语义保持非终态**。等待中的 Loop 对外为 `execution_status=queued`、`is_terminal=false`、`worker_state=waiting_children`、`retryable=false`；`delivered`／`orchestration_complete` 等待条件不把等待稿当最终交付。旧记录的 `NULL` 保留legacy唤醒行为。
5. **不自动修复历史失败任务**。本批只改 Control Plane、兼容迁移和确定性回归，不重放 task 823，不调用完整模型用例，不改变 FAP major、Agent YAML 或凭据边界。

## 后果

- 正面：中间稿可安全落盘并等待 reviewer；已 present 的中间稿也不会提前验收；最终 artifact 门和现有 Tree／Thread／摘要纪律不变。
- 成本：Loop 查询、执行投影与等待条件需同时识别等待标记；调用方只能依据最终交付字段进入验收，不能只看 Loop.status。
- 回归门：覆盖未 present 中间 Run 等待、已 present 中间 Run 等待、子任务齐后同 Loop 唤醒、最终未 present 失败、最终正确交付，以及等待稿不计入 delivered／orchestration_complete。

## 已考虑的备选

- **只在 prompt 强制立即 present**：否决，会把中间稿提前交付并绕过独立审阅。
- **删除 DeerFlow Run 级 artifact 校验**：否决，会破坏普通 Run 的交付完整性；冲突由 Fleet Loop 语义消化。
- **新增 waiting Loop 状态**：本批否决，避免状态机、UI 和旧客户端的大范围迁移；以附加标记保留兼容。
- **自动 present 所有 outputs**：否决，文件存在不代表 Goal 完成，也不能替代验收 actor。
