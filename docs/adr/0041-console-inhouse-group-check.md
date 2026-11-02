# ADR-0041：Console inhouse 判定改为 coin 分组查询

- **状态**：已接受
- **日期**：2026-11-02
- **取代**：ADR-0040 第 1、4 条

## 背景

ADR-0040 要求 DAO access token 携带 `role=inhouse`。实测真实 DAO token 不含该
claim（`role` 为其他值或缺失），合法员工也会被 403。员工归属的权威来源是 coin
的分组接口：`GET https://miao-coin.angrymiao.com/api/group/current-user`，返回
`{"ids":[...]}`，其中 `id=2` 表示内部员工组。

## 决定

1. `/api/console/*` 在既有 JWT 验签（签名 / `iss` / `aud` / `exp` /
   `token_type` / `client_id` / `user_id`）通过后，用**同一个 access token** 调用
   coin 分组接口；`ids` 包含 `FLEET_INHOUSE_GROUP_ID`（默认 `2`）才放行。
2. 配置项：`FLEET_INHOUSE_GROUP_API_URL`、`FLEET_INHOUSE_GROUP_ID`、
   `FLEET_INHOUSE_TIMEOUT_SECONDS`（默认 3）、`FLEET_INHOUSE_CACHE_SECONDS`
   （默认 60）。
3. 分组结果按 token 的 SHA-256 短缓存；失败结果不缓存。
4. 失败语义：验签失败 `401`；token 有效但不在组内 `403`；分组服务不可用或响应
   非法 `503`（fail-closed）。
5. 授权只在 Console backend 执行，不依赖前端解析 token 或隐藏页面；FAP/PAT
   发行、校验与使用路径保持不变。
6. ADR-0040 第 1、4 条（role claim、纯本地验签）被本 ADR 取代；第 2、3 条继续
   有效。

## 后果

- Console 新增对 coin 分组接口的可用性依赖；集群到 `miao-coin.angrymiao.com`
  必须有出口，接口超时或异常时 Console 整体不可用（fail-closed）。
- 用户 access token 会转发给 coin 接口（同一 OAuth 体系、TLS），只存在于请求
  内存，不落盘、不写日志。
- 入组/退组最迟在缓存 TTL（默认 60s）后生效，仍受 token 有效期约束。
- 分组 id `2` 的语义由 coin 侧保持稳定；变更需同步配置。
- 缓存未命中时每次请求多一次网络往返；首屏并发请求可能出现少量重复查询。

## 已考虑的备选

- **继续用 `role=inhouse` claim**：否决；真实 DAO token 不提供该值。
- **仅在前端检查角色/分组**：否决；可直接调用 API 绕过。
- **员工名单落库同步**：否决；没有权威数据源，且增加同步与一致性负担。
- **登录时校验一次**：否决；backend 不参与 code exchange，没有登录事件可挂。