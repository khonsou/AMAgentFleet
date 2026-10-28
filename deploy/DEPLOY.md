# 部署手顺（阿里云 ACK 测试 / ECS Compose 生产）

适用对象：运维团队。本文件是自足的——除两个 GitHub 仓库外不需要任何本机开发环境。

## ACK 集群部署（测试环境）

ACK 测试发布沿用 `E:\timeline\deploy\deploy.sh` 的流程：本地构建镜像、推送 ACR、server-side dry-run，再更新 ACK。它不替代 ADR-0006 的 ECS + Compose 生产部署。

前置条件：WSL Bash、Docker、kubectl、Node.js、已登录 ACR，并能访问 context `kubernetes-admin-c72d7454d43804ccc87327b729e670ba4`。测试默认部署到 `angrymiao-test`，生产命名空间需显式传 `prod`。

首次部署时复制并填写忽略入库的 `deploy/.env`（不要提交或分享该文件）：

```bash
cp deploy/.env.example deploy/.env
# 填入 DEEPSEEK_API_KEY、DATABASE_URL；DAO issuer 默认 angrymiao
bash deploy/deploy.sh test
```

K8s 的 `DATABASE_URL` 是完整远程 PostgreSQL DSN，保存在 namespace Secret `am-agent-fleet-secrets`；已有 Secret 会保留，不会覆盖。建议为 Fleet 单独创建 `deerflow` 数据库，并允许 ACK 网络访问及应用所需的建表/迁移权限；DSN 中的特殊字符需按 URL 编码。DSN 不要带 libpq 专用 `sslmode` 参数，应用使用 asyncpg。脚本不会输出 DSN。Gateway 镜像默认启用 DeerFlow `postgres` extra（安装 `asyncpg`），平台后端镜像包含 Scheduler 所需的 `croniter`。DAO JWT 公钥来自 `deploy/dao-jwt-public-key.pem`，issuer 为 `angrymiao`；脚本将它写入独立 Secret，Console backend 只读挂载公钥。脚本会构建 DeerFlow `am-fleet-base-v2` 网关镜像、平台后端与启用 OAuth 的 Console 前端镜像，推送到 `registry.cn-shenzhen.aliyuncs.com/angrymiao/am-agent-fleet`，随后等待状态 PVC 与 Deployment 就绪。

Kubernetes 不会直接读取 `docker-compose.yml`。发布脚本按相同的构建定义生成镜像，再把镜像 tag 写入 `deploy/k8s/` 清单：网关从 `am-fleet-base-v2` 构建并把 `deploy/config.fleet.yaml` 和 `fleet_tools.py` 加入镜像；control-plane 与 Console backend 共用 `Dockerfile.fleet-python`；前端用 `Dockerfile.console-frontend`。

ACK 部署不创建 PostgreSQL Pod、不拉取 PostgreSQL 镜像，也不创建数据库 PVC；数据库数据由远程 PostgreSQL 服务负责。仍保留一块 20Gi `am-agent-fleet-state` PVC（StorageClass `am-agent-fleet-ssd-retain`：`cloud_ssd`、固定可用区 `cn-shenzhen-a`、立即绑定、删除 PVC 后云盘数据保留），挂到 gateway/control-plane Pod，保存 DeerFlow 的 `.deer-flow` 运行文件、agent memory/facts，以及控制面和网关共享的 MCP 配置。部署脚本会为 `ecs.n1.xlarge` 节点补齐 ACK CSI 的 SSD 能力标签；该实例类型及现有工作负载均已验证支持此类磁盘。磁盘 I/O 对这类文件状态足够，节点池目前都在同一可用区。Pod 被重建时这些文件仍保留；前端、后端代码随镜像发布，不需要数据盘。当前 MCP 凭据实现会落文件，因此 Vault/KMS 接入前不要录入业务凭据。

ACK 测试资源起始值：gateway request `150m CPU / 384Mi`、limit `4 CPU / 1Gi`；control-plane request `50m CPU / 128Mi`、limit `1 CPU / 512Mi`；Console backend request `100m CPU / 128Mi`、limit `500m CPU / 512Mi`；Console web request `25m CPU / 32Mi`、limit `200m CPU / 128Mi`。这些是单副本测试值；当前 Gateway 空闲约 254Mi，活动任务峰值需继续按指标复核。

`docker-compose.yml` 仍保留本机 PostgreSQL 服务供 ECS/单机 Compose 使用；运行 `docker compose up` 仍会拉取 `postgres:16-alpine`。这不影响 ACK 发布脚本，K8s 清单只连接 `DATABASE_URL` 指向的远程数据库。

部署脚本会在鉴权配置和 TLS Secret 校验通过后创建 HTTPS Ingress。域名固定为 `fleet.angrymiao.com`，写在 `deploy/k8s/ingress.yaml`；DAO Client 使用 `agent-fleet`，回调地址为 `https://fleet.angrymiao.com/oauth/callback`，授权服务为 `https://auth.angrymiao.com`，scope 为 `profile phone`，并使用 PKCE。员工登录由 Console backend 验证 RS256 JWT；access token 只保存在浏览器内存中。无需配置办公出口 IP。`prod` 使用 `angrymiao-prod`，需要独立提供 DeepSeek key 和远程数据库 DSN；若 ACR/TLS Secret 不存在，脚本会从 `angrymiao-test` 复制。

当前部署把 gateway 与 control-plane 放在同一个 Pod，共享 DeerFlow 状态和 MCP 配置文件；单副本用于测试，不做自动扩缩。DeerFlow 文件状态存于集群云盘；远程 PostgreSQL 按其服务策略加密与备份。未完成 Vault/KMS 侧 MCP 凭据托管前，不要在该环境注册业务系统凭据。

升级/回滚以镜像 tag 为单位：设置 `IMAGE_TAG` 可复用指定版本；默认从 Git commit 派生 tag，工作区有改动时追加时间戳。`kubectl rollout undo deployment/<name> -n angrymiao-test` 可回滚单个 Deployment。脚本不会删除 PVC。

## 0. 系统组成与上游关系

本仓库（AMAgentFleet）是**基于 DeerFlow 2.x 的二次开发**（上游
[bytedance/deer-flow](https://github.com/bytedance/deer-flow)，MIT License），
关系如下：

| 组件 | 来源 | 说明 |
|------|------|------|
| 网关（执行底座） | [khonsou/deer-flow](https://github.com/khonsou/deer-flow) tag `am-fleet-base-v2` | 包含 Fleet skill evolution 支持；配置文件与 tag 同步，见 ADR-0020 |
| control-plane / Console / fleet_tools | 本仓库 | 平台自有代码 |
| PostgreSQL | `postgres:16-alpine` 官方镜像 | ECS 自建（容器），换 RDS 见 §5.3 |

版本锁定机制：compose 与 ACK 网关镜像按 **git tag** 构建（`am-fleet-base-v2`）。
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

# 4) 观察初始化（control-plane 自动建表、注册 fleet-ops-01 并初始化系统 PAT）
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
- `fleet-ops-vault` 卷：系统 Fleet 操作 PAT 的本地开发／单机部署加密密钥，权限 0600；生产应备份并限制访问，KMS 实现接入 `CredentialVault` 适配边界。原始 PAT 不在卷、环境变量或日志中出现
- `deer-flow-home` 卷：网关运行数据（agent 配置/checkpoint/记忆），**备份对象**

## 4. 部署验证（checklist，全部通过才算完成）

```bash
# 1. 容器健康
docker compose ps                    # 全部 (healthy)
# 2. 网关
curl -s http://<ECS_IP>:8080/ >/dev/null && echo web-ok
docker compose exec gateway python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8001/health').status)"
# 3. 平台 API 与内置 Ops Agent（Registry 和 Gateway 两侧都必须存在）
docker compose exec control-plane python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8003/api/agents/fleet-ops-01').status)"
docker compose exec gateway python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8001/api/agents/fleet-ops-01').status)"
# 4. 业务冒烟（浏览器打开 http://<ECS_IP>:8080 → Registry 应显示 Fleet 操作助手；
#    浮窗查询待分配任务应有明确回答；再注册 website-reader 模板并派发读取任务）
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
`docker build https://github.com/khonsou/deer-flow.git#refs/tags/am-fleet-base-v2 -f backend/Dockerfile`
后 `docker save` 搬运；compose 中注释掉 `build:` 段并引用导入的镜像 tag。

## 6. 安全红线（当前阶段）

1. `DEER_FLOW_AUTH_DISABLED=1` 仅用于集群内 Gateway。Gateway 与 control-plane 使用
   `ClusterIP`，Ingress 只指向 Console web；浏览器访问 `/api/console/*` 必须先通过 DAO
   OAuth JWT 验证。不要新增 Gateway 的公网 Ingress。
2. `.env` 含密钥，永不入库、不入镜像（.dockerignore 已排除）。
3. `PG_PASSWORD` 只供本地 Docker Compose PostgreSQL 使用，ACK 连接远程 PostgreSQL 时不读取它。
4. 数据库与 `deer-flow-home` 卷含全部业务数据与凭据痕迹，纳入备份与加密盘策略。

## 6.1 员工登录（DAO OAuth，angrymiao-auth）

员工（人）侧的鉴权按 Timeline 的 Authorization Code + PKCE 接入：整页跳转，
access token 纯内存保存，不写 localStorage：

```text
员工浏览器 → DAO 授权页（auth.angrymiao.com）→ /oauth/callback → 内存会话
          → 调 /api/console/* 带 Bearer JWT
          → console-backend 本地验签（RSA 公钥 + issuer，不 introspection）
第三方 agent → PAT（FAP /api/*，两套凭据并存，互不混用）
```

启用步骤：

1. DAO Client：`client_id=agent-fleet`，回调 `https://fleet.angrymiao.com/oauth/callback`，
   `allowed_scopes: profile phone`，`require_pkce: true`。当前生产 Auth 配置已登记该 client 和回调。
2. Console backend 使用 `deploy/dao-jwt-public-key.pem` 和 issuer `angrymiao` 本地验签，
   不调用 introspection。部署脚本固定启用前后端鉴权并将验签材料放入 K8s Secret。
3. 只有 `/api/console/health` 为容器探针豁免；产物下载通过带 Bearer JWT 的 fetch+blob
   完成，同样受鉴权保护。

**FAP 面不受影响**：`/api/*`（PAT）与内部 `_cp` 直连链路口径不变。

## 6.2 公网入口检查

- DNS `fleet.angrymiao.com` 指向 ACK Ingress 地址，`am-tls` 证书覆盖该域名。
- DAO Client 已登记精确回调 `https://fleet.angrymiao.com/oauth/callback`。
- Console frontend 构建参数和 backend JWT 验签配置由 `deploy/deploy.sh` 一并启用。
- Gateway/control-plane 不配置公网 Ingress；Console `/api/console/*`（含产物下载）由 DAO JWT 保护。

## 7. 常见操作

```bash
docker compose logs -f gateway         # 网关日志
docker compose up -d --build           # 平台代码更新后重建自有镜像
docker compose restart control-plane   # 配置热更无效时重启 CP（老坑：改代码必须重启）
docker compose down                    # 全停（数据卷保留）
docker compose down -v                 # 全停并清数据（危险）
```
