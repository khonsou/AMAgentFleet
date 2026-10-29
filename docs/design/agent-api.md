# Agent Fleet 功能清单与第三方 Agent 接入协议（FAP v1）

- 状态：FAP v1 已落地（P3b，2026-10-10；H 域全部 ✅，见下表）
- 日期：2026-10-10
- 参考：timeline 项目 change-set 协议 v19.1（[agent-quickstart.mjs](https://github.com/khonsou/timeline/blob/main/examples/agent-quickstart.mjs)）
- 前提：本文档以 **v0.1.0** 现状为基线——任务池（task/loop）、Registry、cron、舰队协作均已落地（ADR-0010、Spike 0002/0003）；FAP 是其上的**新增接入面**，不是返工。

## 1. 设计原则

1. **UI/API 对等**：网站上每一个操作都有对应 API；第三方 agent 与人类员工走同一能力面，只是客户端不同。
2. **自发现优先**：第三方 agent 先 `GET /api/meta` 探明实例能力，再决定能做什么；老实例/缺能力明确报错退出。
3. **鉴权与人同一套**：无独立 agent key 体系——人类走 OIDC SSO（P4），第三方 agent 走 PAT。**注意现状**：网关鉴权当前关闭（`DEER_FLOW_AUTH_DISABLED`），安全组白名单是唯一防线——FAP 暴露公网前鉴权必须先落地。
4. **写操作三件套**：`Idempotency-Key` 幂等、`429 + retry_after` 限速、`source`/`actor` 自报字段透传进审计。
5. **不引入 change-set 两步提交**：验收语义已在任务池层（ADR-0010：task/loop 分离、强制验收），写操作级人审与之冲突且是交互退化。

## 2. 功能清单总表（v0.1.0 基线）

图例：✅ 已落地 · 🔧 FAP 阶段建 · 📅 P4 建 · 🦌 DeerFlow 网关原生

### A. Fleet 总览

| # | 操作 | API | 现状 |
|---|------|-----|------|
| A1 | 全局概览（任务/loop/agent/token） | `GET /api/console/overview` | ✅ Console 私有视图（GUI 数据源，非 FAP 能力面） |
| A2 | fleet 列表（agent 维度状态/成本） | `GET /api/console/fleet` | ✅ Console 私有视图（threads_meta 查询，非 FAP 能力面） |

> 第三方 agent 测试（2026-10-10）核对：控制面 FAP 面现有 29 个端点（openapi 实测），
> A1/A2 及全部状态变更类操作（accept/return/deliver/close/requeue/claim/start/stop/dispatch）
> 均有 FAP 等价端点；Console 私有视图为只读观测 + 网关 D 域包装，不给 agent 属既定设计。

### B. Agent 注册与配置

| # | 操作 | API | 现状 |
|---|------|-----|------|
| B1 | 注册 agent（YAML 一等公民） | `POST /api/registry/agents` | ✅ Spike 0002 |
| B2 | agent 列表 / 配置详情 | `GET /api/registry/agents[/{name}]` | ✅ |
| B3 | 更新配置（模型/skills/MCP/soul/记忆，版本+1） | `PUT /api/registry/agents/{name}` | ✅（模型 profile 自由，ADR-0009） |
| B4 | 版本历史 / 导入导出 | Git 仓库 + `deerflow.custom-agent` 格式 | ✅ |
| B5 | 销毁 agent（级联运行状态） | `DELETE /api/registry/agents/{name}` | ✅ |

启动部署：`fleet-ops-01` 与 `fleet-cmd-01` 均从版本化 YAML 自动对账到 Gateway／Registry。Ops 为只读系统 Agent；指挥官保持可编辑，页面或 API 的修改会写回 YAML，重启不会用仓库旧副本覆盖。

### C. 任务池（task/loop，ADR-0010 已落地）

| # | 操作 | API | 现状 |
|---|------|-----|------|
| C1 | 任务发起（指派 agent 或开放领取） | `POST /api/tasks` | ✅ |
| C2 | 领取（agent/人，竞争非独占，≤3 并行 loop） | `POST /api/tasks/{id}/claim` | ✅ |
| C3 | 任务 / loop 状态与详情 | `GET /api/tasks/{id}`、`GET /api/loops/{id}` | ✅ |
| C4 | 任务列表 + 交付验收（交付并排比较） | `GET /api/tasks?status=&pool_status=&limit=&offset=` | ✅ task 内动作区；稳定倒序分页 |
| C5 | 采纳（task → done，记审计；任何已识别的人或 Agent） | `POST /api/tasks/{id}/accept` | ✅ ADR-0015 |
| C6 | 打回（附意见，同 thread 水合续跑重做；同权） | `POST /api/loops/{id}/return` | ✅ ADR-0015 |
| C7 | 指挥官分解子任务 | runner 工具 `agent_spawn` | ✅ 零改上游 |
| C8 | 人机同构交付（人贴链接交付，同等验收） | `POST /api/loops/{id}/deliver` | ✅ |
| C9 | 异常恢复（断点续跑／重新执行） | `POST /api/loops/{id}/resume`、`POST /api/loops/{id}/requeue` | ✅ ADR-0029；临时故障最多自动重试 2 次，递归异常自动续跑 1 次，人工续跑总上限 3 次 |

### D. Thread 执行与对话

| # | 操作 | API | 现状 |
|---|------|-----|------|
| D1 | 发起执行（阻塞 & SSE 流式） | `POST /api/threads/{tid}/runs/wait|stream` | ✅ 🦌 |
| D2 | thread 状态快照 | `GET /api/threads/{tid}/state` | ✅ 🦌 |
| D3 | 对话消息流 | `GET /api/threads/{tid}/messages` | ✅ 🦌（须 `run_events.backend: db`） |
| D4 | run 详情/事件/中途加入 | `runs/{id}`、`events`、`join` | ✅ 🦌 |
| D5 | run 取消 | `POST .../runs/{rid}/cancel` | ✅ 🦌 |
| D6 | token 用量 | `GET /api/threads/{tid}/token-usage` | ✅ 🦌 |
| D7 | thread 销毁（级联） | `DELETE /api/threads/{tid}` | ✅ |

### E. 调度与触发

| # | 操作 | API | 现状 |
|---|------|-----|------|
| E1 | cron 注册/对账（默认所属 Agent 认领，显式配置可交编排者） | Scheduler 薄层 | ✅ |
| E2 | **第三方 agent 发起/操作任务** | FAP 接入面（本协议 H 域） | 🔧 下一步 |
| E3 | agent 唤醒 agent | `agent_message` / Inbox | ✅ |
| E4 | IM 触发（钉钉/企微） | webhook adapter | 📅（QA-1 待答） |

### F. 授权与凭证

| # | 操作 | API | 现状 |
|---|------|-----|------|
| F1 | 员工 OAuth OBO | Token Vault | 📅 P3/P4 |
| F2 | M2M 服务账号 | Vault 管理 | 📅 P3/P4 |
| F3 | PAT 签发/吊销（第三方 agent 凭证） | `/api/auth/pats` | 🦌 原生表已就绪；🔧 需随 OIDC/鉴权启用一并开放（当前网关鉴权关闭） |

### G. 审计与观测

| # | 操作 | API | 现状 |
|---|------|-----|------|
| G1 | 审计留痕 | `audit_events` | ✅ 留痕已有；📅 查询页/API（P4） |
| G2 | 成本台账（按模型 profile 归集，ADR-0009） | `GET /api/costs` | 📅 P4 |
| G3 | 运行反馈（评分/评论） | `PUT /api/runs/{id}/feedback` | ✅ 🦌 |

### H. 第三方 agent 接入面（FAP，本阶段新增）

| # | 操作 | API | 现状 |
|---|------|-----|------|
| H1 | 自发现（能力/协议版本/文档地址） | `GET /api/meta`（免鉴权） | ✅ P3b |
| H2 | 机器可读文档（面向 LLM 的 markdown 全文） | `GET /api/agent-doc` | ✅ P3b |
| H3 | 协议版本响应头 | 全响应 `X-Protocol-Version` | ✅ P3b |
| H4 | 幂等与限速 | `Idempotency-Key`、`429 retry_after` | ✅ P3b（`fap_idempotency_keys` 24h 窗口；per-PAT/IP 令牌桶 60/min 可配） |
| H5 | 审计透传 | 写操作接受 `source`/`actor` 落 `audit_events.detail` | ✅ P3b |
| H6 | PAT 鉴权接入 | 复用 F3：`/api/pats` 签发/吊销/列表（Console 设置页）+ 中间件 read/write scope 校验 | ✅ P3b（仅内网；公网待 P4 OIDC 协同，ADR-0011 第 5 条） |

## 3. FAP v1 协议约定

### 3.1 自发现

```
GET /api/meta   （免鉴权）
→ {
    "protocol_version": "1.0",
    "server_version": "0.1.0",
    "capabilities": ["tasks", "loops", "threads", "agents_registry",
                     "scheduled_tasks", "feedback", "pat_auth", "idempotency"],
    "doc": "/api/agent-doc"
  }
```

- 所有响应携带 `X-Protocol-Version: 1.0`；客户端预期大版本不一致时告警一次（timeline 同款语义）。
- `GET /api/agent-doc` 返回 markdown 全文：端点、字段、错误模型、示例——面向 LLM 消费，随版本同发。

### 3.2 鉴权

- 人类：OIDC SSO（IdP 待 QA-2，P4）。
- 第三方 agent：`Authorization: Bearer <PAT>`；PAT 由员工在 Console 签发，scope 仅区分 `read`/`write`——**不做数据隔离**（QA-5 全员可见）。
- 前置依赖：网关鉴权当前关闭（安全组白名单唯一防线）；FAP 对外暴露前，OIDC/PAT 校验必须先上线。

### 3.3 写操作纪律

- 变更类 POST 接受 `Idempotency-Key`：同键重试返回首次结果，不重复写入（24h 去重窗口）。
- 限速 `429 + {"error", "retry_after"}`；客户端按 `retry_after` 退避一次，不盲重试。
- 写操作 body 可带 `source: {type: "agent", external_run_id}` 与 `actor: {type, id}`，原样透传进 `audit_events`。
- 创建类响应回 `client_ref → 服务端 id` 映射，供调用方对账。

### 3.4 明确不做的

- change-set 两步提交 + `base_version` 乐观锁：验收已在 task/loop 状态机（ADR-0010），写操作级人审是重复且退化的交互。
- 独立 agent key 体系：与人同一套鉴权语义，少一套凭证面。
- 任务实体新造：直接暴露 ADR-0010 的 `fleet_tasks` / `fleet_task_loops`，不另起模型。

## 4. 验收语义映射（第三方 agent 视角）

第三方 agent 的完整闭环（对应 ADR-0010 状态机）：

```
POST /api/tasks            发起（可指派或开放）
POST /api/tasks/{id}/claim 领取（409 = 已被领满 3 loop，退避或换任务）
...执行（D 域 thread/run API，同 thread 续跑）...
POST /api/loops/{id}/deliver  交付（摘要 + 文件链接 + thread 引用，自动捕获）
GET  /api/tasks/{id}          轮询验收结果（accepted / returned+comment）
returned → 同 thread 续跑修正 → 再 deliver（rounds+1）
```

## 5. 落地顺序

FAP（H 域 + C/E 域的第三方入口）列为 **P3b**，排在阿里云首次部署实测之后、P4（OIDC/成本台账/IM）之前或与其并行；鉴口子（F3/H6）与 P4 OIDC 协同上线。详见设计文档 §9。
