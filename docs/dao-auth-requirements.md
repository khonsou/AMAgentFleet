# DAO 认证能力开发需求（AM Agent Fleet 员工登录）v1.0

> 日期：2026-10-11（v1.0，移交 DAO 开发团队）
> 发起方：Agent Fleet 团队
> 背景：Agent Fleet（ fleet Console，部署至阿里云）接入 angrymiao-auth 统一账号
> 作为**员工（人）的唯一登录方式**。接入方式与 timeline 完全相同（Authorization
> Code + PKCE 纯前端整页跳转，规格见 timeline 的
> `docs/oauth-auth-integration.md`，不重复描述流程细节）。本文档只列 DAO
> 需要提供的能力与材料。

## 0. 我们的使用模型（请先确认理解一致）

```text
员工浏览器 → DAO 授权页登录（PKCE）→ 前端内存持 access token（JWT）
          → 调 fleet Console API 带 Authorization: Bearer <JWT>
          → Console 后端本地验签（RSA 公钥 + issuer 比对，不 introspection 回源）
```

- **token 验证为本地 JWT 校验**，与 coin backend 同一套规格：RS256、
  claims `user_id`(int) / `role` / `exp` / `iss`。这是 timeline
  `dao-auth-requirements.md` 既定的验证原则，我们沿用。
- **第三方 agent 不走本体系**：agent 用平台自有的 PAT（FAP v1，见
  `docs/fap-agent-guide.md`），与员工 OAuth 完全分离，不涉及
  token delegation / token exchange（timeline 的 R1/R2 需求我们不涉及）。
- **纯内存会话**：refresh token 不落盘，页面刷新重新登录是可接受行为
  （timeline 第一期同款取舍），暂不消耗 DAO 的刷新能力。

## R1（P0）：登记 `agent-fleet` Client

与 `timeline` Client 同构，保持如下契约：

```yaml
oauth:
  clients:
    agent-fleet:
      name: Agent Fleet            # 授权页显示名（不要写死到登录页，以 client_name 为准）
      type: h5
      redirect_uris:
        - https://fleet.angrymiao.com/oauth/callback   # 生产（域名待 ECS 采购后终稿，可先登记）
        - https://fleet.angrymiao.com/test/oauth/callback
      allowed_scopes:
        - profile
        - phone
      require_pkce: true
```

- `fleet.angrymiao.com` 为占位域名，ECS 采购后会终稿；按 timeline 的惯例
  **test 环境复用同一 Client**、只追加 redirect 白名单即可，无需另建。
- 前端按实际访问路径生成 `redirect_uri`（与 timeline 同口径）。

## R2（P0）：验签材料（二选一）

**首选：JWKS 端点 URL**（标准 OIDC 做法，推荐）

```text
如 https://auth.angrymiao.com/.well-known/jwks.json
```

我方启动时拉取并缓存公钥，密钥轮换自动跟进，双方零交接成本。

**备选：RSA 公钥 PEM + issuer 值**

- PEM 文本（或文件下发渠道）+ issuer 字符串；
- 若 access token 与 refresh token 签名密钥或 issuer 不同，请分别说明
  （我方第一期只用 access token 验签）。

只要满足：公钥可验 RS256 签名、issuer 可比对、`user_id`(int)/`role`/`exp`
claims 与 coin backend 现状一致。

## R3（P1）：Token Endpoint CORS 确认

我方纯前端跨源调用 `POST https://auth.angrymiao.com/api/oauth/token`
（Origin = fleet Console 域名），请确认 CORS allowlist 支持按已登记 Client
的 redirect origin 放行（timeline 已在使用，预计无需改动，仅需确认）。

## 明确不需要 DAO 做的事

- token delegation / token exchange / RFC 8693（agent 走 PAT，与人完全分离）；
- 业务权限、审批流、审计（平台侧已有审计链）；
- refresh 能力增强（第一期纯内存会话，重新登录即可）；
- 任何为 fleet 定制的登录 UI（授权页统一由 DAO 提供）。

## 问题清单（请 DAO 正式答复）

| # | 问题 | 期望 |
|---|------|------|
| Q1 | JWKS 端点是否可提供？地址与格式？ | R2 首选；若否，走备选 PEM+issuer |
| Q2 | `agent-fleet` Client 登记预计时间？redirect 白名单后续追加流程？ | R1 |
| Q3 | Token Endpoint 的 CORS 是否按 Client origin 自动放行？ | R3 确认即可 |
| Q4 | access token 的 `iss` 值与 coin backend 现行配置是否一致？ | 影响我方 issuer 校验配置 |

## 我方已就绪（联调前自验通过）

- 前端 PKCE 登录闭环（含 state/verifier 校验、纯内存会话）；
- 后端 RS256 本地验签中间件（PyJWT，iss/exp/user_id 校验，伪造 token 与
  错 issuer 自签均已实测 401）；
- 本地 mock DAO 端到端 harness（`scripts/mock-oauth.mjs`），DAO 侧材料
  到位后只需替换环境变量即可切真。
