# 生产部署手顺（阿里云 ECS + Docker Compose）

适用对象：运维团队。本文件是自足的——除两个 GitHub 仓库外不需要任何本机开发环境。

## 0. 系统组成与上游关系

本仓库（AMAgentFleet）是**基于 DeerFlow 2.x 的二次开发**（上游
[bytedance/deer-flow](https://github.com/bytedance/deer-flow)，MIT License），
关系如下：

| 组件 | 来源 | 说明 |
|------|------|------|
| 网关（执行底座） | [khonsou/deer-flow](https://github.com/khonsou/deer-flow) tag `am-fleet-base-v1` | fork 仅作本项目的部署基线镜像来源，不向上游贡献；基线 = 上游 `89c9f08`（2026-10-08），验证方式见 fork 的 FLEET.md |
| control-plane / Console / fleet_tools | 本仓库 | 平台自有代码 |
| PostgreSQL | `postgres:16-alpine` 官方镜像 | ECS 自建（容器），换 RDS 见 §5.3 |

版本锁定机制：compose 里网关镜像按 **git tag** 构建（`am-fleet-base-v1`）。
升级底座 = fork 仓库 rebase 到新上游基线 → 打新 tag → 改 compose 里的 tag →
`docker compose up -d --build gateway`，之后必须重过 §4 验收。

## 1. 前置条件

- ECS ×1（MVP all-in-one，建议 2 vCPU / 8 GiB / 40 GiB 盘；2-ECS 拆分见 §5）
- Docker ≥ 24、Docker Compose ≥ 2.24
- 安全组：仅放行 `FLEET_PORT`（默认 8080），来源白名单限定办公出口 IP；
  **22 端口不对公网开放**
- 一个 DeepSeek 官方 API key

## 2. 部署步骤

```bash
# 1) 取部署资产（只需 deploy/ 目录，也可整仓克隆）
git clone https://github.com/khonsou/AMAgentFleet.git
cd AMAgentFleet/deploy

# 2) 配置
cp .env.example .env
# 填写 DEEPSEEK_API_KEY、PG_PASSWORD（其余默认值可先用）

# 3) 启动（首次构建网关镜像约 10–20 分钟，含 uv/pnpm 依赖）
docker compose up -d --build

# 4) 观察初始化（control-plane 首次启动自动建表）
docker compose logs -f control-plane
```

## 3. 架构与端口

```
                 ┌─────────────────────────── ECS ───────────────────────────┐
 办公网(白名单) ──▶│ console-web:80 ──/api/──▶ console-backend:8002          │
                 │        │                    │ 透传                        │
                 │        ▼                    ▼                            │
                 │   静态前端              control-plane:8003 ──┐            │
                 │        │                    │                │           │
                 │        ▼                    ▼                ▼           │
                 │   gateway:8001 ◀── fleet_tools.py (协作工具)            │
                 │        │                                                 │
                 │   postgres:5432 ◀── 平台库（agent/任务池/审计/网关事件）   │
                 └──────────────────────────────────────────────────────────┘
```

- 对外仅 `console-web`（`${FLEET_PORT}`）；网关 :8001、CP :8003、后端 :8002 均为容器网络内部
- **FAP 同源暴露**：第三方 agent 的 API 入口 = Console 网址本身
  （`http://<host>:${FLEET_PORT}`）。console-backend 把非 `/api/console/*`
  的 `/api/*` 原样转发给 CP，并重建 `X-Forwarded-For`（取直接对端 IP、
  忽略客户端自带值）。CP 侧仅信任 `FAP_TRUSTED_PROXIES`（默认
  本机 + RFC1918 内网段，compose 已配）的 XFF，其余一律不信——远程客户端
  伪造 XFF 无法冒充内部流量。平台内部直连（console-backend `_cp`、
  dispatcher）无 XFF + 可信对端 = 内部豁免；**经代理的客户端流量一律要 PAT**。
- `deerflow-cfg` 共享卷：CP 渲染 `extensions_config.json`（MCP 注册表），网关读取
- `deer-flow-home` 卷：网关运行数据（agent 配置/checkpoint/记忆），**备份对象**

## 4. 部署验证（checklist，全部通过才算完成）

```bash
# 1. 容器健康
docker compose ps                    # 全部 (healthy)
# 2. 网关
curl -s http://<ECS_IP>:8080/ >/dev/null && echo web-ok
docker compose exec gateway python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8001/health').status)"
# 3. 平台 API
docker compose exec control-plane python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8003/api/templates').status)"
# 4. 业务冒烟（浏览器打开 http://<ECS_IP>:8080 → Registry → 注册 website-reader 模板
#    （填目标站点域名）→ 拉起 → 对话派发一个读取任务 → Fleet 看板验收）
```

验证标准对应 spike 0003 回归项：agent 拉起、YAML 下发网关、任务派发 token 落库、
交付捕获、强制验收。若第 4 步不通，按 `docker compose logs gateway` →
`control-plane` 顺序排查。

## 5. 变体

### 5.1 两台 ECS 拆分（ADR-0006 原始拓扑）

- **ECS-B（数据）**：仅跑 postgres
  ```bash
  cd deploy && docker compose up -d postgres
  ```
  （给 postgres 加 `ports: ["5432"]` 或在 ECS 内网直连；安全组仅放行 ECS-A 内网 IP）
- **ECS-A（应用）**：其余服务。compose 中把 `*pg-dsn` 的 `@postgres` 改为
  `@<ECS-B内网IP>`（`postgres:5432` 字样在锚点定义处，改一处全部生效）

### 5.2 网关多副本

默认单 worker。扩容前阅读上游
`docker/docker-compose.yaml` 中 gateway 服务的注释：多 worker 要求
`run_ownership.heartbeat_enabled: true` + Redis stream bridge，MVP 不建议。

### 5.3 PostgreSQL 换阿里云 RDS

1. 创建 RDS PostgreSQL 16，白名单放行 ECS 内网 IP
2. compose 的 `*pg-dsn` 锚点改为 RDS 连接串
3. `docker compose up -d`（postgres 服务可删除或保留作本地备胎）

### 5.4 离线/内网构建

网关镜像构建需访问 github.com（git context）。纯内网环境：在有网机器
`docker build https://github.com/khonsou/deer-flow.git#refs/tags/am-fleet-base-v1 -f backend/Dockerfile`
后 `docker save` 搬运；compose 中注释掉 `build:` 段并引用导入的镜像 tag。

## 6. 安全红线（当前阶段）

1. **网关鉴权处于关闭状态**（`DEER_FLOW_AUTH_DISABLED=1`，MVP 决策；P4 OIDC
   落地后改 0）。在此之前，安全组来源白名单是唯一防线，不得放宽。
2. `.env` 含密钥，永不入库、不入镜像（.dockerignore 已排除）。
3. `PG_PASSWORD` 轮转：改 `.env` 后 `docker compose up -d`（pg-data 卷中的旧
   口令不会自动同步，需进库 `ALTER USER` 或重建数据卷）。
4. 数据库与 `deer-flow-home` 卷含全部业务数据与凭据痕迹，纳入备份与加密盘策略。

## 7. 常见操作

```bash
docker compose logs -f gateway         # 网关日志
docker compose up -d --build           # 平台代码更新后重建自有镜像
docker compose restart control-plane   # 配置热更无效时重启 CP（老坑：改代码必须重启）
docker compose down                    # 全停（数据卷保留）
docker compose down -v                 # 全停并清数据（危险）
```
