# ADR-0038：第三方执行接入的单凭证、终态、批量读取与取消语义

- **状态**：已接受
- **日期**：2026-11-02

## 背景

GEO 第三方团队对真实公网实例完成注册、派发与模型执行后，报告了七类集成问题：write PAT 不能读、部署与路由不可发现、递归失控、`claimed + error` 无法区分终态、逐 Task 轮询触发 429、close 未传播取消、服务端幂等缺少联合验收。报告证明的是接入契约缺口，不是 GEO 业务结果，也不构成扩大 GEO 面板或启用 cron 的依据。

内部 Loop 回 `claimed` 是为了保留恢复与人工重派能力，不能直接改成外部终态；另一方面，只归档数据库而不停止 Runtime Run 会把“取消请求”误报成“执行已经停止”。

## 决定

1. **单 PAT 连续读写**：`write` 隐含 `read`，独立 `read` PAT 仍为只读观察者，不新增第三套 scope，不扩大任何数据权限。
2. **外部执行语义与内部状态解耦**：Task／Loop 响应增加 `execution_status`、`is_terminal`、`retryable`、`failure_kind`、`last_error`、`next_retry_at`、`worker_state`。凡有错误且没有后续恢复动作，包括 transient／recursion 预算耗尽，都映射为 `failed`；有恢复动作才是可等待的自动重试。内部 Loop 状态机不变。
3. **批量状态读取**：`GET /api/tasks?ids=...` 一次读取 1–100 个 Task，使用 SQL 参数化过滤；保留现有限速和 `Retry-After`，本批不新增 SSE／long-poll。
4. **close 必须确认取消**：先对活动 Run 调用 Runtime cancel 并等待确认，再归档 Loop、终结 Task。`cancel_requested_at` 与 `cancelled_at` 分开记录；只有 Runtime 返回停止确认才写后者。不支持取消、取消失败或仅返回 202 未确认时，Task 不归档。成功响应与审计包含 `cancelled_runs`。
5. **能力发现对应当前进程**：`/api/meta` 增加 API／部署版本、method×scope 矩阵、限速说明、真实 capabilities 与 `public_operations`。公开 operation 取协议清单与当前 FastAPI 路由的交集，只含 Control Plane PAT 面；`runtime_cancel` 仅在当前 Runtime 实际支持时出现。部署版本由 `FLEET_DEPLOY_VERSION` 提供，缺省回退 API 版本，不包含秘密。
6. **本批边界**：不在同一变更中加入 `max_wall_seconds`／工具调用硬预算、queue position、Worker 可观测或 `agent_config_version`；这些与真实服务端幂等联合验收进入后续明确任务。Timeline 继续走 ADR-0037 的既有 YAML／Gateway 能力。

## 后果

- 第三方可用一个 write PAT 完成“读取模型 → 派发 → 批量收集 → 交付／关闭”，并仅凭结构化字段区分失败、重试和正常排队。
- FAP 采用向后兼容字段扩展，协议 major 不变；旧客户端仍可读取内部状态，但新客户端不得再用 `claimed + error` 猜终态。
- close 在不支持取消的 Runtime 上可能显式失败，这是有意的安全门闩，不允许以数据库归档伪造停止成功。
- 专项 14 项与 Control Plane 178 项离线回归通过；21 项需显式 PG 测试环境的既有集成用例跳过。本地真实 API 冒烟已验证单 write PAT、PG 批量查询、幂等回放／冲突与 close；真实活动 Run 已确认 `cancelled_runs=1` 且 Runtime 为 `interrupted`。生产部署版本与第三方六题闭环仍须部署后联合验收，不能用本地结果代替。

## 已考虑的备选

- **给 Loop 增加 failed 状态**：本批否决；会破坏既有恢复语义，且外部投影已能表达当前尝试终态。
- **维持 read/write 双 PAT**：否决；执行器天然需要连续读写，双令牌只增加轮换和审计成本。
- **close 发出取消后立即归档**：否决；202 只代表请求被接受，不能证明模型执行已经停止。
- **直接公开 Console／Gateway 全部路由**：否决；第三方只依赖 Control Plane 的稳定 Runtime surface，避免部署内部路径成为公共契约。
