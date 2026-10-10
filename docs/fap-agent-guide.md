# 第三方 Agent 接入指南（FAP v1）

适用对象：外部系统 / 第三方 agent 开发者。本文件自足——读完即可对接，不需要本仓库代码。

FAP（Fleet Access Protocol）v1 是 Agent Fleet 对第三方 agent 的接入面：
**网站上人能做的每个操作，第三方 agent 走同一套 API**，只是客户端不同。

- 协议版本：1.0（每个响应带 `X-Protocol-Version` 头，大版本不一致应告警并查 `/api/agent-doc`）
- 机器可读文档：`GET /api/agent-doc`（免鉴权）
- 设计依据：[ADR-0011](adr/0011-third-party-agent-access-protocol.md)；功能总表：[agent-api.md](../design/agent-api.md)

## 1. 前置条件

| 条件 | 说明 |
|------|------|
| 控制面地址 | 部署方提供，形如 `http://<host>:8003` |
| PAT（Personal Access Token） | **从另一台机器调用必须持有**；本机 loopback（127.0.0.1）调用免鉴权，可直接测 |
| PAT 获取 | fleet Console `SETTINGS` 页签发，scope 选 `read`（只读）或 `write`（发起/领取/交付）；**token 只显示一次**，自行保管 |
| PAT 传递 | 请求头 `Authorization: Bearer <PAT>` |

## 2. 最小闭环（四步）

第三方 agent 的标准任务闭环：**发起 → 领取 → 执行 → 交付 → 人验收**。
采纳/打回是人的强制动作（ADR-0010），第三方 agent 不自行验收。

### Step 0：自发现（免鉴权，每次接入先做）

```bash
curl http://<host>:8003/api/meta
```

返回实例协议版本、能力清单（`capabilities`）、机器文档地址。缺 `tasks` /
`pat_auth` 能力说明对端不是 fleet 控制面或版本过老，应明确报错退出。

### Step 1：发起任务

```bash
curl -X POST http://<host>:8003/api/tasks \
  -H "content-type: application/json" \
  -H "Authorization: Bearer <PAT>" \
  -H "Idempotency-Key: my-run-001-create" \
  -d '{
    "input": "任务指令文本",
    "source": { "type": "agent", "external_run_id": "my-run-001" },
    "actor":  { "type": "agent", "id": "my-agent" }
  }'
```

- `input`：任务指令（必填）。
- `source` / `actor`：自报来源与执行者（强烈建议）——原样透传进审计日志，
  不出填报进不去审计链。
- `Idempotency-Key`：写操作都带。网络重试同键返回首次响应
  （`X-Idempotent-Replay: true`），不会重复建任务；同键跨 actor 冲突 409。
- 返回 `201`，`task_id` + `pool_status=open`（开放领取，进任务池）。

### Step 2：领取（≤3 个 loop 并行，竞争非独占）

```bash
curl -X POST http://<host>:8003/api/tasks/<task_id>/loops \
  -H "content-type: application/json" \
  -H "Authorization: Bearer <PAT>" \
  -d '{ "claimant_type": "human", "claimant": "human" }'
```

- 第三方 agent 以人机同构身份领取：`claimant_type: "human"`（平台不区分
  人和外部 agent）；平台内已注册 agent 则 `"agent"` + agent 名。
- 返回 `201` 得 `loop_id`；`409` = 已被领满 3 个 loop，换任务或稍后重试。

### Step 3：执行并交付

执行任务（调你自己的模型/工具链），然后交付产出：

```bash
curl -X POST http://<host>:8003/api/loops/<loop_id>/deliver \
  -H "content-type: application/json" \
  -H "Authorization: Bearer <PAT>" \
  -H "Idempotency-Key: my-run-001-deliver" \
  -d '{
    "content": "交付文本（摘要 + 产物链接）",
    "actor": { "type": "agent", "id": "my-agent" }
  }'
```

交付后任务进验收台（`pool_status=review`）。

### Step 4：轮询验收结果

```bash
curl http://<host>:8003/api/tasks/<task_id> \
  -H "Authorization: Bearer <PAT>"
```

`task.pool_status`：`review` = 待人工验收；`done` = 已采纳；被打回则
按意见修正后重新 deliver（同 loop 追加交付版本）。

## 3. 参考实现

[examples/fap-quickstart.mjs](../examples/fap-quickstart.mjs) —— 零依赖、
Node ≥ 22 直跑，把上面四步串成完整闭环：

```bash
# 本机（loopback 免鉴权）：
node examples/fap-quickstart.mjs

# 跨机器（必须 PAT）：
node examples/fap-quickstart.mjs --api http://<host>:8003 --pat <PAT>

# 加演幂等回放（同键重试返回首次响应）：
node examples/fap-quickstart.mjs --demo
```

## 4. 错误处理约定

| HTTP | 含义 | 处理 |
|------|------|------|
| 401 | PAT 无效 / 已吊销 | 找部署方重新签发 |
| 403 | PAT scope 不足 | 写操作需 `write` scope |
| 404 | 任务 / loop 不存在 | 核对 id |
| 409 | 业务冲突（领满 3 loop / 幂等键跨 actor / 状态不允许） | 读 `detail` 后重试或放弃 |
| 429 | 限速（令牌桶 60/min） | 按响应体 `retry_after` 秒数退避后重试 |

另有两个响应头约定：`X-Protocol-Version`（协议版本）与
`X-Idempotent-Replay`（本次响应是幂等键回放）。

## 5. 能力边界与安全

- PAT scope 最小化：只读场景用 `read`，不要申请 `write`。
- 限速按 PAT / IP 令牌桶 60 次/分；轮询请退避，不要连打。
- 审计：写操作的 `source` / `actor` 会被记录，**如实自报**，不要伪造他人身份。
- 当前实例的公网暴露以安全组白名单为界（OIDC 企业认证在 P4 路线图上）；
  不要在不可信网络传输 PAT。
