# ADR-0028：Thread 实例选模、Task 继承与原生 Managed Models

- **状态**：已接受
- **日期**：2026-10-28

## 背景

ADR-0009 已开放 per-agent 默认模型，但复杂任务需要同一 Agent 定义的不同运行实例使用不同模型。任务发起人还需要决定：由舰队指挥官按子任务选模，或强制所有后代实例继承指定模型。与此同时，多 Provider／多 API Key 管理不能诱发明文密钥进入 Thread、协议响应或 Git。

DeerFlow 已提供 `/api/models` 只读目录、`context.model_name` 单次运行覆盖，以及加密存储、密钥不回显的 `/api/managed-models` 管理面。重复建设一套 Profile 与凭证数据库会造成模型目录分裂。

## 决定

1. **模型绑定实例而非定义**：Agent YAML 的 `spec.agent.model` 仅是默认值；每个 agent Loop 在领取时解析并持久化 `model_profile`、`model_snapshot` 与 `model_selected_by`，Dispatcher 以 `context.model_name` 传给 Gateway。打回续跑沿用 Loop 快照。
2. **统一入口**：`tasks.create`、`agents.dispatch`、`tasks.loops.claim`、`agent_spawn` 与 cron 均接受可选 `model_profile`。解析顺序为实例显式值 → Agent 默认值 → Gateway 首个 Profile。旧请求省略新字段时语义不变。
3. **用户保留控制权**：Task 增加 `subagent_model_policy=auto|inherit`，默认 `auto`。`auto` 允许指挥官选择后代模型；`inherit` 要求根 Task 显式指定 Profile，并将其固定传播给所有后代，冲突请求在入队前拒绝。
4. **白名单约束**：Agent YAML 可增加可选 `model_policy.allowed_profiles`；缺省允许所有已登记 Profile，保持向后兼容。未知 Profile 返回 422，越权或继承冲突返回 403／409，失败不得留下已入队 Loop。
5. **复用原生模型目录**：Settings 的模型与密钥管理直接代理 DeerFlow Managed Models；每个受管 Profile 可保存独立 API Key。现有 `$DEEPSEEK_API_KEY` 与 YAML Profile 保持系统默认和只读，不迁移、不回显。受管 Profile 的新增／更新／测试为 human-only，Ops 与第三方 PAT 不得调用。
6. **秘密边界**：协议只传 Profile 名，不传 API Key 或任意 Provider URL。Managed Models 的响应不得含密钥明文；编辑时留空保留原密钥。模型审计只记录 Profile、选择者与来源，不记录密钥。
7. **范围限制**：本期不实现任意 URL 授权、运行中热切换、独立 AI 自动路由器或另一套凭证服务。生产密钥的 KMS 托管沿用 Vault 演进方向，不把本地开发加密文件误称为 KMS。

## 后果

- 正面：同一 Agent 可并行运行不同模型；用户与指挥官职责清晰；复用 Gateway 原生能力，避免重复凭证库和底层 fork 修改。
- 负面／约束：Loop 领取增加模型目录读取；Gateway 不可用时不能可靠解析新实例。Managed Models 的加密密钥必须纳入持久卷与备份，丢失后受管 Profile 无法恢复。
- 兼容：新增 YAML 与 API 字段均可选，不升级 `apiVersion`；已有 Task、Loop 和 cron 不需要迁移输入。

## 已考虑的备选

- **在 Agent YAML 固定模型**：否决，无法支持同定义多实例不同模型。
- **只让指挥官通过自然语言选模**：否决，用户无法形成可校验、可审计的硬约束。
- **控制面另建 Profile 与 API Key 数据库**：否决，与 Gateway 原生 Managed Models 重复且易产生配置不一致。
- **允许协议传任意 Provider URL／Key**：否决，绕过出口、审计与秘密隔离。
