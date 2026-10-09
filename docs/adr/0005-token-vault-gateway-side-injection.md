# ADR-0005：授权采用 Token Vault + 网关侧令牌注入，不建审批流

- **状态**：已接受
- **日期**：2026-10-08

## 背景

业务系统均有 OAuth：普通 agent 以人类员工授权（OBO）行动；少数超级 agent 对支持 M2M 的系统用服务账号。业务方确认人工审批需求弱（agent 以分析、建议、规划、跟踪为主）。同时必须假设 prompt injection 会发生——2026 年事故复盘的主结论是最大风险为"过大的凭证 + 开放的出口"，而非容器逃逸。

## 决定

1. **Token Vault**：员工 OAuth refresh token 与 M2M client secret 存 PG 加密列（KMS），按 `员工×系统` 管理；员工离职即吊销。
2. **网关侧令牌注入（借鉴 ACP credential masking）**：runner 调 MCP 工具只携带 `(agent_id, thread_id, 发起人身份)`；MCP Gateway 用 Vault 中的 refresh token 换 15–60 分钟短期 access token，**令牌在 Gateway 侧使用，全程不进 runner 上下文**。
3. **工具白名单**在 agent YAML 中声明（`allow`/`deny`），Gateway 每次调用校验并写审计。
4. **不建审批流**；安全兜底 = 白名单最小化 + 只读优先的工具上架策略 + 全量审计 + 写操作工具单独评审。
5. Runner 容器**默认拒绝出口**，白名单仅含 DeepSeek API、MCP Gateway、Vault、公司内网。

## 后果

- 正面：被注入的 agent 拿不到原始 token，只能表达"白名单内的调用意图"；攻击面大幅收敛。
- 残余风险（如实记录）：被注入的 agent 仍可在员工权限内发起白名单内调用。缓解依赖白名单评审纪律；"agent 可执行写操作清单"列为治理事项，定期评审。
- 约束：Gateway 成为唯一接触业务凭证的组件，其自身加固与审计是安全评审重点。

## 已考虑的备选

- 短期令牌注入 runner 环境变量（v2 方案）：已被本 ADR 取代——令牌出 Vault 即有泄漏面。
- 高危操作人工审批流：本期否决（业务确认需求弱）；`approvals` 字段在 YAML schema 中保留，未来启用不改数据模型。
