# Spike 0002：ADR-0008 开门验证（agents_api 组织级复用）

- **状态**：✅ 已完成（2026-10-09，结论：**通过**，Registry 按"组织级包装层"路线推进）
- **工时预算**：半天（实际约 40 分钟）
- **关联**：[ADR-0008](../adr/0008-reuse-deerflow-agents-api-for-per-agent-config.md) 第 4 条验证项、设计文档 §1-9/§1-12

## 环境与前提

- 网关：spike/deer-flow 本地 uvicorn（`DEER_FLOW_AUTH_DISABLED=1`），PG 用 spike 内嵌 pgserver
- 配置变更（`spike/deer-flow/config.yaml`，将列入部署强制项）：
  - `agents_api.enabled: true`
  - `scheduler.enabled: true`（P2 Scheduler 复用其 cron 引擎的前提）

## 验证项与结果

| # | ADR-0008 验证项 | 结果 |
|---|----------------|------|
| 1 | `agents_api.enabled=true` 端到端 CRUD + agent 会话跑通 | ✅ 通过 |
| 2 | user-scoped 语义与"公司统一 fleet"的映射方案 | ✅ 通过，方案落地（见下） |
| 3 | `agents/` YAML 与 agents_api 存储的主次关系 | ✅ 通过，YAML 为一等公民（见下） |
| 4 | 定时任务用户级语义能否承载平台 cron + 投递绑定 | ✅ 通过（见下） |

### 1. agents CRUD + 会话（验证细节）

- `GET /api/agents` 初始为空 → `POST /api/agents` 创建 `ar-reconciliation-01`（display_name 中文、model 覆盖 `deepseek-chat`、`model_settings.temperature=0.1`、SOUL 护栏）→ `GET` 单查 → `PUT` 全量更新（temperature 0.1→0.2、max_tokens=8192）→ `DELETE` 204 → list 为空 → `POST /api/agents/import` 用导出包恢复成功。全套语义符合预期（更新是 PUT 全量语义，PATCH 返回 405）。
- **模型白名单强制生效**：PUT `{"model":"gpt-4"}` 返回 422 `Unknown model 'gpt-4'. Use a model name defined under models: in config.yaml.` —— ADR-0003/0009 的"限 config.yaml 已声明 profile"不需要平台侧额外代码，底座自带。
- **会话绑定**：`POST /api/runs/wait`，`assistant_id="ar-reconciliation-01"` + 新 thread_id。Agent 按 SOUL 回答（"我是应收对账助手……不能做过账操作"），token 计量正常（prompt 8691 / completion 30）。**custom agent 经 assistant_id 直通运行时已验证**。
- **导出格式**：`GET /api/agents/{name}/export` 返回 `{"format":"deerflow.custom-agent","version":1,"agent":{...,"memory_enabled":true}}`，extra=forbid 的版本化文档，可直接作为 `agents/` 仓库的 YAML 载体。

### 2. user-scoped → 单一共享组织作用域

- 存储落点：`.deer-flow/users/default/agents/ar-reconciliation-01/`。无认证上下文时 `get_effective_user_id()` 回落 `DEFAULT_USER_ID="default"`（`packages/harness/deerflow/runtime/user_context.py:98-110`），即**天然单一存储桶**。
- **映射方案（落地结论）**：fleet 的所有 agent 配置读写只经自建 Console/Admin API 以单一服务身份调用 agents_api（生产为 VPC 内服务账号；公网入口只有 Console，员工不直连网关）。全员可见 = 全部读写落在同一存储桶，fleet 看板天然无权限过滤；审计仍记操作人（Console 层）。无需改 DeerFlow 源码。
- 风险备注：`users/default` 桶与"真实用户桶"并列存在；若未来 DeerFlow 官方前端被直接开放给员工，会产生第二入口绕过 fleet 视图——维持 ADR-0008 决定 2（官方前端不作员工入口）即可。

### 3. YAML ↔ agents_api 主次关系

- 结论：**`agents/` Git 仓库为一等公民**；部署 = 将 YAML（导出包格式）import 进 agents_api；对账 = 定期 export 与仓库 diff（drift detection）。与 ADR-0008 第 4 条建议一致。
- 导入幂等性未验证（同名 import 是覆盖还是报错）——列为 Registry 实现时的细节项。

### 4. 定时任务语义（Scheduler 复用评估）

- 创建绑定 custom agent 的 cron 任务：`POST /api/scheduled-tasks`，字段含 `assistant_id`（校验必须是已注册 custom agent）、`schedule_type: cron` + `schedule_spec.cron`、`timezone`（校验 IANA）、`context_mode: fresh_thread_per_run`（默认，**每次触发开新 thread，正好满足 §7.4"一任务一 thread"**）、`max_runs` / `end_at` / `stop_condition`（"stop when..." 自然语言停跑条件）。
- **准时触发实测**：cron `29 11 * * *`（Asia/Shanghai），`scheduled_for=11:29:02`、同秒启动、`status=success`、summary="pong"、`total_tokens=6961` 落表、自动开新 thread。
- 引擎能力：poll 5s、lease 120s（崩溃可重认领，`multi_instance: true` 支持多实例）、全局与 per-user 并发上限、queue_timeout、`recursion_limit: 1000`（scheduled runs 默认 1000，高于 web 默认 100）。
- **结论：P2 不自建触发引擎**。平台 Scheduler = 薄层：解析 `agents/` YAML 的 cron → 调 scheduled-tasks API 建/改/删任务 + 任务与 agent 版本绑定对账。DeerFlow 官方 cron 语法与 YAML 示例 `37 8 * * 1-5` 一致（5 段 cron）。
- 缺口（P3+）：IM 渠道投递（notification_delivery outbox 已有框架，绑定 channels）、"agent 唤醒 agent"的 Inbox 语义（不在 scheduled-tasks 内，仍按 §7.3 自建）。

## 结论

ADR-0008 方向确认可行，无不可调和冲突，**不回退备选 A**。Registry 按"组织级包装层"开工；DeerFlow 官方前端维持不作员工入口；`agents_api.enabled` + `scheduler.enabled` 列入部署强制配置。

## 遗留细节（带入 P2 实现）

1. import 同名 agent 的幂等/覆盖语义待验证。
2. `recursion_limit` 已确认纳入 per-agent 配置（scheduled runs 默认 1000；web 路径仍需按 spike 0001 结论配置）。
3. scheduled task 与 agent 配置版本绑定：agent 更新后，已建定时任务引用的是 assistant_id（实时读最新配置）——Registry 需决定是否锁版本（建议 P2 先不锁，YAML 对账时提示）。
