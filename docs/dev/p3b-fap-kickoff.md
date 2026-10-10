# P3b 开工说明：FAP v1 第三方 Agent 接入面（给 coder）

- 日期：2026-10-10
- 范围：设计文档 §9 的 **P3b** 行，决策依据 [ADR-0011](../adr/0011-third-party-agent-access-protocol.md)
- 前置阅读（按序）：[README](../../README.md) → [AGENTS.md](../../AGENTS.md) → [ADR-0010](../adr/0010-task-loop-pool-mandatory-acceptance.md)（任务池语义）→ [ADR-0011](../adr/0011-third-party-agent-access-protocol.md) → [agent-api 功能清单与协议](../design/agent-api.md)（**本文档的 H 域就是任务清单**）

## 0. 一句话目标

让任何一个第三方 agent **仅凭 `/api/meta` 和一个 PAT**，从零完成"自发现 → 鉴权 → 发起任务 → 领取 → 执行 → 交付 → 查询验收结果"全流程，且所有写操作幂等、可审计。

**本阶段明确不做**：OIDC SSO 全量（P4）、成本台账（P4）、IM adapter（P4）、change-set 两步提交（已否决，ADR-0011）、任务实体新造（复用 ADR-0010 的 `fleet_tasks`/`fleet_task_loops`）。

## 1. 现状资产（全部可读复用）

| 资产 | 位置 | 说明 |
|------|------|------|
| 控制面入口 | `control-plane/app/main.py`（FastAPI，`version="0.2.0"`，端口 8003） | FAP 中间件与端点挂这里 |
| 任务池 | `control-plane/app/pool.py` | task/loop 状态机、领取/交付/验收（ADR-0010） |
| Registry | `control-plane/app/registry.py` | agent 注册/版本化 |
| 审计 | `control-plane/app/db.py` 的 `audit()` helper | source/actor 透传的落点 |
| 网关转发 | `db.py` 的 `GATEWAY` 常量 → :8001 | thread/run 类端点的转发模式照此 |
| runner 工具 | `fleet_tools.py`（`agent_spawn`/`agent_message`） | 第三方入口不需要碰 runner |
| DeerFlow PAT 表 | 网关 DB `personal_access_tokens` | F3 的载体；网关路由 `/api/auth/pats` 原生存在 |
| 协议参照实现 | [timeline agent-quickstart.mjs](https://github.com/khonsou/timeline/blob/main/examples/agent-quickstart.mjs) | 自发现/429 退避/幂等键/409 重建的客户端范式 |

## 2. 工作分解（按序，每步独立验收）

### 任务 1：自发现与协议头（H1/H2/H3）

- `GET /api/meta`（免鉴权）：返回 `protocol_version`/`server_version`/`capabilities[]`/`doc`，格式照 [agent-api.md](../design/agent-api.md) §3.1；
- 中间件：所有 `/api/*` 响应加 `X-Protocol-Version: 1.0`；
- `GET /api/agent-doc`：返回 markdown 全文（端点/字段/错误模型/示例），先手写一版，随版本维护；capabilities 出现变化时同步更新。

**验收**：`curl /api/meta` 返回合规 JSON；任意 API 响应带协议头；agent-doc 内容覆盖 H+C+D 域端点。

### 任务 2：幂等中间件（H4 上半）

- 新表 `fap_idempotency_keys(key, actor, method, path, response_json, created_at)`，24h 去重窗口；
- 写操作 POST 携带 `Idempotency-Key` 时：同键同 actor 直接回放首次响应（不重复执行）；键冲突语义照 ADR-0011；
- 先覆盖 C 域写端点（tasks 发起/领取/交付/验收），再铺到 Registry 写端点。

**验收**：同一 `Idempotency-Key` 重试 `POST /api/tasks`，只创建一条任务，两次响应体一致。

### 任务 3：限速（H4 下半）

- 简单令牌桶：per-PAT（无 PAT 时 per-IP），默认 60 req/min 可配；
- 命中返回 `429 + {"error", "retry_after"}`。

**验收**：连续打 70 个请求，出现 429 且 `retry_after` 合理；按值等待后恢复。

### 任务 4：审计透传（H5）

- 写操作 body 接受可选 `source: {type, external_run_id}` / `actor: {type, id}`；
- 经 `db.audit()` 落 `audit_events`；Console 审计页不在本阶段，能 SQL 查出即可。

**验收**：带 source/actor 创建任务，`audit_events` 里能查到原样透传的字段。

### 任务 5：PAT 鉴权接入（H6 + F3）⚠️ 硬门槛

- 控制面加 PAT 校验中间件：读操作要 `read` scope，写操作要 `write` scope（DeerFlow 原生 PAT 无 scope 时在控制面自建薄表 `fap_pat_scopes` 补足）；
- **现状警示**：网关运行在 `DEER_FLOW_AUTH_DISABLED=1`，安全组白名单是唯一防线。本任务完成前，FAP 端点**只允许绑定 127.0.0.1 / 内网**；公网暴露必须等 OIDC/PAT 全链路（与 P4 协同，见 ADR-0011 第 5 条）；
- Console 加一个轻量 PAT 签发入口（设置页：生成/吊销/列列表）。

**验收**：无 PAT 调写端点 401；`read` scope PAT 调写端点 403；合法 PAT 全流程通。

### 任务 6：参考客户端脚本 + 端到端验收

- 写 `examples/fap-quickstart.mjs`（零依赖 Node ≥22，范式照 timeline 脚本）：meta 自发现 → PAT → 发起任务 → 领取 → 交付 → 轮询验收；
- 用它在干净环境跑通 ADR-0011 的验收标准。

**验收**：脚本零先验知识（只给地址+PAT）跑通全流程；大版本不一致时有告警；429/409 路径有演示。

## 3. Definition of Done（对齐 §9 P3b 验收行）

1. 第三方脚本仅凭 `/api/meta` + PAT 完成：自发现 → 发起任务 → 领取 → 交付 → 验收查询 全流程；
2. 幂等键重试不重复写入；429 带 `retry_after`；
3. 审计可见 source/actor；
4. 无鉴权公网暴露 = 零（任务 5 的门槛未被绕过）；
5. 文档同步：[agent-api.md](../design/agent-api.md) H 域状态 🔧→✅，README 当前状态；新决策一律新增 ADR。

## 4. 协作纪律

- ADR 不可变，变更只能新增；与 [agent-api.md](../design/agent-api.md) 清单冲突的端点先改清单再写码；
- 密钥/PAT 不进 git、不进日志、不进文档正文；
- 每任务完成后跑一遍现有全栈冒烟（Console 发起任务→领取→交付→验收）确认无回归；
- 卡壳先看控制面与网关日志，任务池语义问题先重读 ADR-0010 再动手。
