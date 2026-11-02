# ADR-0042：运行时 Capability Plane、Agent 感知、共享工作空间与自适应编排

- **状态**：已接受
- **日期**：2026-11-02

## 背景

当前 Registry 记录 Agent 配置，但声明能力、Registry 版本、Runtime 生效能力和 Task 编排之间缺少统一契约。用户需要理解具名 Agent 的工具与权限，简单能力缺失还会被模型包装成正常交付。仅增加某个第三方网站适配、按名称选择 Agent 或限制指挥官／操作助手的业务能力，都不能形成通用 Fleet。

Fleet 的目标不是预先设计业务访问方案，而是让用户或第三方 Agent 在运行时注册能力，再由所有 Agent 发现、组合并调用这些能力，完成单个 Agent 难以完成的工作。

## 决定

1. **平台不绑定业务资源**：平台只定义通用 Provider／Capability／Artifact／Workspace／ExecutionPlan 契约，不在代码、数据模型或 operation 中写死任何第三方网站、业务系统或专用访问流程。资源地址、授权句柄和业务 operation 均由运行时注册。
2. **所有 Agent 默认能力最大化**：新 Agent 默认 `capability_policy=inherit_all` 与 `inherit_new_capabilities=true`。指挥官、操作助手和普通 Agent 都能发现、调用、组合和委派能力；受限配置只用于用户显式选择的特殊场景，不作为默认业务模板。
3. **运行时动态注册**：本地 Agent、MCP、远程 Agent 通过 Provider Manifest 注册 operation、输入输出 Schema、Artifact 类型、风险、资源引用、授权句柄和版本。新增能力不得要求修改 Fleet 源码或既有 Agent YAML。
4. **声明与生效分离**：Provider 记录 declared、verified、health、version 及实际调用表现。平台不得仅凭描述推定能力可用，也不得让配置漂移静默进入执行。
5. **统一两类调用**：确定性操作通过 `capabilities.invoke` 调用；需要推理和多步骤的工作通过 `agents.spawn` 委派。GUI、指挥官、操作助手和第三方 Agent 使用同一调用面，不设置隐藏特权旁路。
6. **Agent 感知**：Provider／Agent Card 同时包含静态能力画像和动态健康、成功率、延迟、成本、失败与恢复记录。Resolver 依据输入输出 Artifact、实际可用性、风险和运行表现选择候选，不只依赖名称或自然语言描述。
7. **共享工作空间**：复杂 Goal 使用版本化 Workspace 保存 Goal、约束、反目标、停止条件、能力快照、ExecutionPlan、Task DAG、Evidence、Artifact、Decision、Effect 和预算。完整产物独立持久化，Agent 获得有界摘要和可追踪引用，不复制秘密或无差别全量 transcript。
8. **自适应混合编排**：ExecutionPlan 是有向无环图，节点声明所需 capability、输入引用、输出 Artifact、验收、回退和预算。编排支持并行扇出／汇聚、独立评价、失败重路由和依据 Evidence 的显式重规划；禁止无停止条件的自动扩张。
9. **运行治理代替能力阉割**：统一鉴权、幂等、预算、取消、恢复、审计和生命周期管理。秘密正文仍只由 Vault／Gateway 保管；能力最大化不等于秘密扩散或不可停止。
10. **能力失败结构化**：`CAPABILITY_MISMATCH`、`CAPABILITY_UNAVAILABLE`、`CONFIG_STALE`、`AUTH_REQUIRED`、`PROVIDER_FAILED`、`EXECUTION_FAILED` 与正常交付分开。能力缺失不得直接计为成功 Goal。

## 首批实现边界

- 实现通用 Provider Manifest、动态注册、Resolver、统一 invoke、Provider Card、版本化 Workspace 和 DAG 执行。
- 离线 Hard Case 只使用显式启用的本地 mock provider，覆盖并行、共享 Evidence、独立核验、失败回退和重规划；mock 不得冒充真实业务基线。
- 真实 provider 通过统一 Adapter 接口接入；未实现的 Adapter 必须明确返回不支持，不得伪造调用成功。
- 首批不启用 cron、不自动创建无限后续计划、不开放生产写入，不宣称所有外部 Adapter 已完成。

## 后果

- 正面：新能力可以在不改平台代码的情况下被发现和组合；用户只面对 Goal；复杂混合执行可恢复、可评价。
- 负面／约束：需要维护 Schema、Artifact 兼容性、运行画像和 Workspace 一致性；动态 provider 增加验证与审计成本。
- 后续：真实 provider 的通用探针、更多 Artifact 类型、成本感知调度和模型辅助规划，均以本批通用契约为基础，不回退为业务专用适配。

## 已考虑的备选

- **为每个外部系统增加专用 Agent／API**：否决，无法运行时扩展，并形成业务硬编码。
- **只增强指挥官的工具**：否决，会把能力集中在少数角色，普通注册 Agent 仍被降级。
- **只按 Agent 名称和描述路由**：否决，无法验证实际能力、版本和组合兼容性。
- **共享全部原始对话**：否决，成本和秘密风险不可控；改为共享版本化事实、产物、决策和引用。
