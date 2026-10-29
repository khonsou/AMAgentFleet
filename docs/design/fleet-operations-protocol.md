# Fleet Operations Protocol v1

- 状态：已接受（ADR-0017/0018，2026-10-28）
- 机器可读真相：[`fleet-ops-protocol.json`](../../fleet-ops-protocol.json)
- 自发现：`GET /api/meta` → `operations_protocol`
- 完整清单：`GET /api/ops/protocol`

## 1. 目标与范围

协议覆盖当前 Fleet 的全部可操作功能，而非预设若干对话闭环。人类页面、`fleet-ops-01` 与第三方 Agent 使用相同实体和状态机：

| 域 | 能力 |
|---|---|
| 系统与观测 | 自发现、健康、总览、Thread 舰队视图、审计 |
| Agent | 列表、详情、注册、完整配置更新、启动、休眠、销毁、兼容派发 |
| Task | 创建、列表、详情、评论、关闭、待验收队列 |
| Loop | 认领、交付、打回、断点续跑、重新执行、Thread 反查 |
| Thread / Run | 新建、对话、消息、状态、产物、销毁、执行、事件、取消、反馈、token |
| 调度与唤醒 | YAML cron、手动对账、Inbox 收发 |
| 接入配置 | MCP 列表/注册/更新/删除/重渲染，模板列表/渲染 |
| 第三方凭证 | PAT 列表、签发、吊销 |

cron 的创建、修改与删除通过 Agent 完整 YAML 的 `triggers.cron` 完成，不另造调度实体。缺省由所属 Agent 执行；显式 `orchestrator` 才交编排者（ADR-0016）。

## 2. 调用模型

每个操作有稳定 `id`，例如 `tasks.create`、`agents.update`、`loops.return`。调用方提交：

```json
{
  "operation_id": "tasks.create",
  "path_params": {},
  "query": {},
  "body": {"input": "检查应收异常", "assignee": "ar-reconciliation-01"},
  "confirmed": true
}
```

`path_params` 只填充清单声明的路径变量；执行器根据 operation ID 确定服务面、方法和路径，模型不能任意指定 URL。响应保留 HTTP 状态与原始结果；写后应读取对应资源核实。

## 3. 通用约束

1. **实体语义**：Agent YAML 是定义；Task 是业务与验收实例；Loop 是执行单元；Thread 是 Agent 运行会话；Run 是一次图执行。
2. **统一任务模型**：业务执行仍走 Fleet Task／Loop，交付必须进入验收；操作助手聊天本身不创建业务 Task。
3. **读操作**：`write` 缺省为 `false`，可直接执行。
4. **写操作**：`write: true`，必须先向用户展示动作和关键参数并获得明确确认，再带 `confirmed: true` 调用。
5. **秘密边界**：`confirmation: human_only` 的操作由人前往页面完成；含非空 `secrets` 的 MCP 请求同样不得经 Agent 执行。PAT token、业务 token 和密钥不得写入 Thread、文件、日志或审计正文。
6. **幂等与冲突**：外部 FAP 写请求携带唯一 `Idempotency-Key`；409 表示状态冲突，应重读资源而非盲目重试；429 按 `retry_after` 退避。
7. **不猜测**：ID、状态和配置必须来自实际读取。协议没有登记的操作不得绕过执行器访问。

## 4. 系统操作 Agent 与统一鉴权

`fleet-ops-01` 默认注册在 `control-plane/agents/`，专属 `tool_groups: ["fleet:ops"]`，无通用 HTTP MCP 权限。它负责操作 Fleet，不承担舰队指挥官的业务编排职责。系统 Agent 的日常配置与生命周期只读，只能由激活脚本走版本化部署原语升级。

本地浮窗和第三方 Agent 统一由 PAT scope 授权，Agent 名称不是权限依据。Runner 只把 operation ID 与参数提交给控制面内部执行代理；代理从服务端 Vault 取系统 PAT 并注入 Control Plane 请求，原始 token 不进入 Runner、Thread、YAML 或日志。系统 PAT 固定 `read+write`，不能经普通接口冒用、吊销或降权，只能在 Settings 经人工确认轮换且不回显 token。

全局浮窗只是该 Agent 的 Web Thread 客户端；协议不依赖浮窗，第三方 Agent 也可按同一清单工作。浮窗身份失效时明确报错并重建 `fleet-ops-01` Thread，不回退到 `lead_agent`。

## 5. 激活与全局浮窗

本地服务启动后执行：

```bash
console/backend/.venv/bin/python scripts/fleet_ops_activate.py
```

脚本通过 Registry 版本化部署原语幂等注册／更新 `fleet-ops-01`，同时核对 Registry、Gateway、完整 YAML 和运行中协议，并发起一次真实只读模型调用；未实际调用 `fleet_ops_request` 或运行出错都会失败。

全站右下角“舰队操作助手”是该 Agent 的持久 Thread 浮窗。首次打开只创建空白 Thread，首条消息仍通过正常发送路径执行，避免重复运行。Thread ID 保存在浏览器本地，关闭、跨页面或刷新后恢复；失效会话可重新连接。浮窗不包含逐操作表单或另一套业务逻辑，普通聊天不创建 Task，写操作由 Agent 先说明动作和参数，工具层仍强制 `confirmed=true`。

## 6. 自动化验收

```bash
# 全部：确定性回归 + 在线激活 + 真实模型 + 67 操作 + 浏览器
bash scripts/test_fleet_ops_all.sh

# 仅确定性回归，不需要服务与模型
bash scripts/test_fleet_ops_all.sh --offline

# 单独重跑在线 67 项矩阵
console/backend/.venv/bin/python scripts/fleet_ops_acceptance.py
```

在线矩阵由协议 operation ID 驱动，覆盖 Agent 生命周期、Task／Loop 真实交付与验收、Thread／Run、SSE、事件、取消、反馈、Token、产物下载、cron 对账、MCP、PAT read/write scope、系统 PAT 状态与人工轮换、幂等回放与冲突、409／429 和吊销。每轮使用唯一测试资源，结束后停止活动任务并销毁 Agent、MCP、Thread 和 PAT；已验收任务按审计要求保留。报告写入 `artifacts/fleet-ops-acceptance.json`，只含操作状态，不含响应正文或凭证。

重派测试会显式准备滞留 `returned` Loop 夹具：真实 Agent Loop 打回后自动续跑，不能把该瞬时状态当作确定性重派条件。PAT 限速与吊销分别使用独立凭证，避免先于鉴权的 429 遮蔽 401。

## 7. 覆盖性维护

任何新增 Control Plane API、页面独占操作或网关执行能力，必须同步更新 `fleet-ops-protocol.json`。回归测试会对照 FastAPI 路由与清单，漏项直接失败。协议 major 版本在不兼容变更时升级。
