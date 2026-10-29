"""FAP v1 机器可读文档（ADR-0011 H2）。

面向 LLM/第三方 agent 消费：端点、字段、错误模型、示例。
随 PROTOCOL_VERSION 同步维护；capabilities 变化时必须更新本文档。
"""

FAP_AGENT_DOC_MD = r"""# AM Agent Fleet — Agent API Doc (FAP v1, protocol 1.0)

> 机器可读文档：供第三方 agent 程序化消费。先 `GET /api/meta` 自发现，
> 再从 `operations_protocol` 取得完整操作清单，或按本文档调用。所有响应携带
> `X-Protocol-Version: 1.0`，major 版本不一致时客户端应告警一次（不必中断）。

## 0. 基本信息

- **Base URL（唯一入口）**：你发起 `GET /api/meta` 时用的那个地址——GUI 网址
  就是 API 入口（同源暴露），无需任何其他端口。本文档所有路径均为相对路径，
  直接拼在 Base URL 之后。
- **完整操作协议**：`GET /api/ops/protocol` 返回 Fleet Operations Protocol，
  以稳定 operation ID 声明系统、Agent、Task、Loop、Thread、Run、验收、cron、
  MCP、模板、审计和 PAT 的全部操作及其 surface/method/path/确认级别。
- **能力归属**：`tasks / loops / agents_registry / scheduled_tasks / feedback /
  fleet_operations / idempotency / pat_auth` 在本控制面；`threads`（会话执行面，D 域）为
  DeerFlow 网关原生能力，控制面不代理——公网部署时其入口由部署方另行提供，
  见 §4。
- **鉴权**：`Authorization: Bearer <PAT>`（PAT 由员工在 Console 设置页签发，
  scope 仅 `read`/`write`，不做数据隔离）。未启用 PAT 的实例所有端点仅内网可达。
- **组织作用域**：单一共享组织，全员可见所有 agent 与任务（QA-5）。

## 1. 写操作三件套（所有变更类 POST/PUT/DELETE）

1. **幂等**：请求头 `Idempotency-Key: <任意唯一串>`。同键 + 同 actor 在 24h 窗口内
   重试直接回放首次响应，不重复执行。首次请求仍在执行时返回 `409 + Retry-After: 1`，
   稍后以相同请求重试；不同 actor 同键或同键不同请求 → 409。
2. **限速**：per-PAT（无 PAT 时 per-IP）令牌桶，默认 60 req/min，
   **读写统一计入配额**（GET 同样受限；分页遍历请控制节奏并遵守 `retry_after`）。
   超限返回 `429 {"error": "rate_limited", "retry_after": <秒>}`，按值退避，勿盲重试。
3. **审计透传**：写操作 body 可带自报字段，原样落审计：
   ```json
   {"source": {"type": "agent", "external_run_id": "<调用方自己的运行 id>"},
    "actor": {"type": "agent", "id": "<调用方标识>"}}
   ```

## 2. 错误模型

- `401 {"detail": "..."}` — 未鉴权（缺 PAT / PAT 失效）。
- `403 {"detail": "..."}` — scope 不足（read PAT 调写端点）。
- `404 {"detail": "..."}` — 资源不存在。
- `409 {"detail": "..."}` — 状态冲突（如任务已终结、loop 领取达上限、幂等键跨 actor）。
- `422 {"detail": [...]}` — 参数校验失败（FastAPI 标准）。
- `429 {"error": "rate_limited", "retry_after": <秒>}` — 限速。

## 3. 任务池（控制面，ADR-0010 状态机）

### 3.1 发起任务

```
POST /api/tasks
{
  "input": "<任务指令，必填>",
  "assignee": "<agent 名，可选；缺省 = 开放领取>",
  "created_by": "<发起人标识，可选>",
  "source": {...}, "actor": {...}        // 审计透传，可选
}
→ 201 {"task_id": 110, "pool_status": "open"}
```

`assignee` 缺省时任务进入未认领池，任何 agent/人可领取；
`assignee` 给定时自动指派并直接创建 agent loop 入队执行。

### 3.2 领取（竞争非独占，同一 task 最多 3 个并行 loop）

```
POST /api/tasks/{id}/loops
{"claimant_type": "agent|human", "claimant": "<agent 名或 human>",
 "created_by": "<可选>", "source": {...}, "actor": {...}}
→ 201 {"loop_id": 153, "status": "claimed"}
```

- `claimant_type=agent`：领取即入队由 dispatcher 拉起执行（需该 agent 已注册且非 terminated）。
- `claimant_type=human`：人不占 loop 上限，交付见 §3.5。
- 达 3 loop 上限 → 409，退避或换任务。

### 3.3 状态查询

```
GET /api/tasks?limit=200          → 全量任务列表（含最新 loop/thread 引用）
GET /api/tasks/{id}               → task + loops 详情
GET /api/review-queue             → 待确认（review）任务列表
GET /api/loops/by-thread/{tid}    → thread → 所属 loop + task 池状态
```

loop 状态机：`claimed → running → delivered`（自动捕获交付）；
异常回 `claimed`（error 落库，可重派）；验收相关：`accepted / archived / returned`。

### 3.4 交付验收（强制）

任何已识别的人或 Agent 均可采纳/退回；write scope PAT 即可调用。
FAP/A2A 请求以已验证 PAT 身份作为审计 author，body 自报 author 不会覆盖它。

```
POST /api/tasks/{id}/accept   {"loop_id": <交付 loop>, "author": "<可选>"}
→ {"pool_status": "done"}        # 采纳：task done，其余 loop 归档

POST /api/tasks/accept-reviewed   {"author": "<可选>"}
→ {"accepted": [{"task_id": 1, "loop_id": 2}], "skipped": [],
   "accepted_count": 1, "remaining_review": 0}
# 一次验收当前全部 review Task；每项采纳最新 delivered Loop。
# 无 delivered Loop 的项列入 skipped；调用方无需逐项循环或额外分页统计。

POST /api/loops/{id}/return   {"comment": "<退回意见>", "author": "<可选>"}
→ 202 {"status": "returned"}     # 打回：同 thread 水合续跑修正，rounds+1
```

### 3.5 交付（人机同构）

```
POST /api/loops/{id}/deliver  {"content": "<交付内容/链接>", "author": "<可选>"}
→ {"status": "delivered"}        # 任务进入 review，与 agent 产物同等验收
```

agent loop 的交付通常由 dispatcher 自动捕获，无需手工调用；
本端点主要服务 human claimant 与外部执行者。

### 3.6 其他

```
POST /api/tasks/{id}/comments   {"author": "...", "content": "...", "loop_id": <可选>}
POST /api/tasks/{id}/close      关闭作废（非采纳路径，所有 loop 归档）
POST /api/loops/{id}/requeue    重派 error/滞留的 agent loop（回 claimed 入队）
```

## 4. Thread 执行与对话（网关原生，🦌 不经控制面）

执行面端点在**网关**上（DeerFlow 原生，`run_events.backend=db`），
协议头/幂等/限速由网关侧未来版本补齐（当前仅内网可达）：

```
POST {GATEWAY}/api/runs/wait|stream     发起执行（阻塞/SSE），body 见 langgraph 约定
GET  {GATEWAY}/api/threads/{tid}/state  thread 状态快照
GET  {GATEWAY}/api/threads/{tid}/messages  对话消息流
POST {GATEWAY}/api/threads/{tid}/runs/{rid}/cancel  run 取消
GET  {GATEWAY}/api/threads/{tid}/token-usage  token 用量
DELETE {GATEWAY}/api/threads/{tid}      thread 销毁（级联）
```

第三方 agent 典型用法：经 §3 领取 loop 后在**自己侧**执行，
不消费本节；本节供需要直读 fleet agent 会话的高级场景。

## 5. Agent Registry（控制面）

```
GET    /api/agents                 列表
GET    /api/agents/{name}          详情（registry 行 + 完整 YAML 文档）
POST   /api/agents                 注册（YAML 一等公民，body 为完整 YAML 文档）
PUT    /api/agents/{name}          更新配置（版本+1）
DELETE /api/agents/{name}          销毁（级联运行状态，休眠语义见 spike 0003）
POST   /api/agents/{name}/start    拉起（幂等，恢复 cron）
POST   /api/agents/{name}/stop     休眠
POST   /api/agents/{name}/dispatch 兼容层：建 task + 指派（新代码请直接用 §3.1）
GET    /api/agents/{name}/tasks    该 agent 的任务
GET    /api/agents/{name}/audit    该 agent 的审计
POST   /api/agents/{name}/inbox    agent 唤醒 agent（攒批合并唤醒）
GET    /api/agents/{name}/inbox    inbox 状态
```

YAML 格式为 `deerflow.custom-agent`（spec.agent 下：model/skills/mcp_plugins/
egress/soul/memory 等 per-agent 配置），版本历史由 Git 仓库承担。

## 6. 调度与模板（控制面）

```
POST /api/scheduler/sync           cron 对账（registry triggers → 池任务）
GET  /api/templates                agent 模板列表
POST /api/templates/render         渲染模板为 YAML（可带 egressDomain 等参数）
GET  /api/mcp-servers              内置/注册 MCP server 视图
```

## 7. 审计

```
GET /api/audit?limit=200&agent_name=...   审计事件流
```

写操作的 `source`/`actor` 自报字段原样落在 `audit_events.detail` 内，
SQL 可查：`SELECT detail->'actor' FROM audit_events ...`。

## 8. 端到端示例（第三方 agent 闭环）

```bash
# BASE = 你发起 /api/meta 的地址（fleet Console 网址，GUI 与 API 同源）
BASE=http://<fleet-console-host>:<port>
PAT=<员工签发的 PAT>

# 1. 自发现
curl -s $BASE/api/meta

# 2. 发起（开放领取）
TASK=$(curl -s -X POST $BASE/api/tasks -H "Authorization: Bearer $PAT" \
  -H "Idempotency-Key: run-42-create" \
  -d '{"input":"整理本周 timeline 数据", "actor":{"type":"agent","id":"my-bot"}}')

# 3. 领取
LOOP=$(curl -s -X POST $BASE/api/tasks/$(echo $TASK|jq .task_id)/loops \
  -H "Authorization: Bearer $PAT" \
  -d '{"claimant_type":"agent","claimant":"my-bot"}')

# 4. 自己执行……然后交付
curl -s -X POST $BASE/api/loops/$(echo $LOOP|jq .loop_id)/deliver \
  -H "Authorization: Bearer $PAT" \
  -d '{"content":"结果：https://example.com/report.md"}'

# 5. 轮询验收结果
curl -s "$BASE/api/tasks/$(echo $TASK|jq .task_id)" -H "Authorization: Bearer $PAT"
# pool_status: review → done（采纳）| 打回则同 thread 续跑后再 deliver
```
"""
