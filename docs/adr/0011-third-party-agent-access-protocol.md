# ADR-0011：第三方 Agent 接入协议——自发现 + 单一能力面 + 复用任务池验收

- **状态**：已接受（提案，P3b 实施）
- **日期**：2026-10-10

## 背景

业务方要求：fleet 平台网站上的**所有操作**（任务发起、agent 注册、任务领取、对话、验收、状态获取等）都要能被第三方 agent 程序化访问，并参考 timeline 项目的自发现协议（change-set 协议 v19.1：`GET /api/meta` 免鉴权自发现、`X-Protocol-Version` 响应头、幂等键、429 退避、source/actor 审计透传、机器可读文档端点）。

现状（v0.1.0）：任务池（ADR-0010 task/loop/强制验收）、Registry、cron、`agent_spawn`/`agent_message` 协作均已落地；但**控制面尚无自发现能力、幂等中间件与统一协议版本**（已核实代码），且网关鉴权当前关闭（安全组白名单为唯一防线）。

## 决定

1. **单一能力面，UI/API 对等**：Console 网站与第三方 agent 消费同一套控制面 API；网站没有特权接口。功能清单与端点映射维护在 [../design/agent-api.md](../design/agent-api.md)。
2. **采用 timeline 协议的自发现骨架**（FAP v1）：
   - `GET /api/meta` 免鉴权：`protocol_version` / `server_version` / `capabilities[]` / `doc`；
   - 全响应带 `X-Protocol-Version`，breaking 变更升 major；
   - `GET /api/agent-doc` 返回面向 LLM 的 markdown 全文；
   - 写操作 `Idempotency-Key` 幂等（24h 去重）、`429 + retry_after` 限速；
   - 写操作接受 `source`/`actor` 自报字段，透传进 `audit_events`。
3. **不采用 change-set 两步提交与 `base_version` 乐观锁**：验收语义已由 ADR-0010 的 task/loop 状态机承接（强制验收、打回续跑）；写操作级人审是重复且退化的交互。
4. **不新建任务实体**：第三方入口直接暴露 `fleet_tasks` / `fleet_task_loops` 的既有状态机。
5. **鉴权与人同一套**：人类 OIDC SSO（P4）；第三方 agent 用 PAT（复用 DeerFlow `personal_access_tokens`），scope 只分 read/write，不做数据隔离（QA-5）。**前置依赖**：FAP 对外暴露前，网关鉴权（OIDC/PAT 校验）必须先上线——当前 `DEER_FLOW_AUTH_DISABLED` 状态不可带 FAP 出公网。
6. 实施列为 **P3b**：排在阿里云首次部署实测之后，与 P4（OIDC/成本台账）协同。

## 后果

- 正面：第三方 agent 零文档成本接入（meta 自发现 + agent-doc）；幂等与审计透传从协议第一天就位；验收闭环复用任务池，无重复模型。
- 负面：控制面新增自发现/幂等/协议头中间件一层自研；PAT 的 read/write scope 需在 DeerFlow 原生 PAT 上加薄校验。
- 风险绑定：FAP 的公网价值依赖鉴权落地，与 P4 OIDC 形成硬耦合排期。

## 已考虑的备选

- 照搬 timeline change-set 全协议（含 pending 人审）：否决——与 ADR-0010 强制验收模型重复，弱审批场景下是交互退化。
- 第三方 agent 直连 DeerFlow 网关原生 API：否决——无任务池/Registry 组织语义、无自发现面，暴露面过大不利审计收敛。
- 另建 agent 专用 key 体系：否决——两套凭证面双倍运维与对账成本。
