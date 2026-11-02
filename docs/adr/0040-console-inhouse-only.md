# ADR-0040：Console 仅允许 inhouse 员工

- **状态**：已接受
- **日期**：2026-11-02

## 背景

Console 的 DAO OAuth 中间件已验证签名、issuer、audience、有效期、Fleet client 绑定与 `user_id`，但未检查账号是否属于公司员工。任何能取得 Fleet access token 的 DAO 账号都可访问 Console API，不符合员工控制台边界。

## 决定

1. `/api/console/*` 在既有 JWT 认证后强制要求 `role=inhouse`；角色缺失或为其他值返回 `403`，认证失败仍返回 `401`，健康检查豁免不变。
2. 授权只在 Console backend 强制执行，不依赖前端解析 token 或隐藏页面。
3. FAP/PAT 发行、校验与使用路径保持不变；员工发行的 PAT 可继续由持有者使用，不要求每次 PAT 请求附带 DAO token。
4. 本期仍采用本地 JWT 验签，不新增 introspection；角色与在职状态以 token 签发时为准，token 到期后重新登录取得新状态。

## 后果

- 非 inhouse DAO 账号即使持有签名、受众均正确的 Fleet access token，也不能调用 Console API。
- DAO 必须稳定提供 `role=inhouse` 契约；角色撤销不是实时的，仍有既有 token 有效期窗口。
- 员工 OAuth 与第三方 PAT 的既有分工不变，不影响 ADR-0011、ADR-0018 与 ADR-0038 的第三方执行链路。

## 已考虑的备选

- **仅在前端检查角色**：否决；可直接调用 API 绕过。
- **所有 PAT 请求也强制 DAO inhouse token**：否决；会破坏已确认的第三方 PAT 使用方式。
- **每次请求回源 introspection**：本期不引入；保留现有本地验签与可用性边界，后续如有实时在职要求再单独决策。
