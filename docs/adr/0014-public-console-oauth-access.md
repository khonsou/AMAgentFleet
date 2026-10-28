# ADR-0014：Console 公网入口使用 DAO OAuth

- **状态**：已接受
- **日期**：2026-10-19

## 背景

ACK 测试环境需要通过 `fleet.angrymiao.com` 从公网访问。Console 已有 DAO OAuth 前端流程和后端 JWT 验签，但 Ingress 原先要求来源 IP 白名单，且产物下载接口暂时绕过 JWT。

## 决定

1. 使用 DAO Authorization Code + PKCE；Client 为 `agent-fleet`，回调固定为 `https://fleet.angrymiao.com/oauth/callback`。
2. Console backend 本地验证 DAO RS256 JWT，并强制校验生产 issuer；所有 `/api/console/*` 仅健康检查端点豁免。
3. 创建 HTTPS Ingress 不依赖来源 IP；Gateway 与 control-plane 保持 ClusterIP，不得新增公网路由。
4. 产物下载改用带 Bearer JWT 的 fetch，不再匿名访问；FAP `/api/*` 继续使用 PAT。

## 后果

- 员工可从任意网络经统一账号登录 Console；access token 仅驻留浏览器内存。
- DAO 生产 RSA 公钥用于本地验签，issuer 与 Auth 配置保持一致；密钥轮换后需更新 Secret 并重启 backend。
- Gateway 本身仍不做员工 OAuth 验证，因为它不经 Ingress 暴露；新增公网路由前必须另行设计鉴权。

## 已考虑的备选

- 来源 IP/CIDR 白名单：否决，出差网络变化会阻断员工访问。
- 直接把 Gateway 暴露到公网：否决，Gateway 的认证方式不同于 Console 员工 OAuth。
