# 发布说明（运维团队）

面向运维的**发版操作手册**。首次部署请先看 [DEPLOY.md](DEPLOY.md)，本文件管
「之后的每一次发布」。两份文件 + `.env.example` 注释足以完成全部操作，无需
开发环境。

## 1. 仓库地图（到底用哪个仓库）

| 仓库 | 角色 | 怎么用 | 谁会动它 |
|------|------|--------|----------|
| **[khonsou/AMAgentFleet](https://github.com/khonsou/AMAgentFleet)** | 平台主仓库（本仓库） | ECS 上部署的就是它；运维只读 + 按 tag 拉取 | 开发团队维护；运维按 §2 发布 |
| **[khonsou/deer-flow](https://github.com/khonsou/deer-flow)** | 网关底座 fork（上游 bytedance/deer-flow，MIT） | **不直接部署**；compose 按其 git tag 构建网关镜像 | 仅开发团队做底座升级（§3.2）；**不向上游贡献**（见 fork FLEET.md 用途声明） |
| 镜像 `am-fleet-gateway:*` / `am-fleet-python:1` | 部署产物 | ECS 本机 `docker compose build`，无公共镜像仓库 | — |

一句话：**运维日常只碰 AMAgentFleet 的 tag；deer-flow 仓库对运维只读透明。**

## 2. 版本约定

- 平台版本 = AMAgentFleet 的 **git tag**（语义化版本，`v1.0.0`、`v1.1.0`…）。
  开发团队打 tag 即视为「可发布」。
- 底座版本 = deer-flow fork 的 tag（`am-fleet-base-vN`），由 compose 文件引用，
  随平台仓库一起演进——**运维不需要单独感知底座版本**。

## 3. 发布流程

### 3.1 平台发布（常规，开发团队告知「发 vX.Y.Z」后执行）

```bash
cd AMAgentFleet/deploy
git fetch --tags
git checkout vX.Y.Z            # 工作区务必停在 tag，不跟 main 漂移
docker compose up -d --build   # 重建有变化的自有镜像并滚动重启
docker compose ps              # 全部 healthy
```

随后按 [DEPLOY.md §4](DEPLOY.md) 验收 checklist 执行；第 4 项（业务冒烟）
建议每次发布都跑一遍——它覆盖 spike 0003 全链路（注册→拉起→派发→交付→验收）。

### 3.2 底座升级（少见；开发团队主导，运维配合）

1. 开发在 fork 上 rebase 到新上游基线、重过 spike 0001/2/3 回归、打新 tag
   `am-fleet-base-vN+1`
2. 平台仓库 compose 里网关的构建 tag 改为新版本（随某个平台 tag 一起发布）
3. 运维按 §3.1 正常发布即可；**网关镜像会全量重建（约 10–20 分钟）**，
   安排低峰期

### 3.3 回滚

平台代码回滚（数据库结构未变时）：

```bash
git checkout v上一个版本
docker compose up -d --build
```

注意：
- **数据库结构不自动回滚**。CP 的建表语句是增量 `CREATE TABLE IF NOT EXISTS`，
  新增列/新表对已发布代码向后兼容；若发布说明里标注了「含不兼容 schema 变更」，
  回滚前必须先按开发提供的回滚 SQL 处理，或直接连库备份后重建
- 回滚后跑一次 DEPLOY.md §4 的 1–3 项（容器健康 + 网关/CP API）

## 4. 发布前检查清单（运维自查）

- [ ] 拿到开发给的**目标 tag** 与**变更摘要**（尤其是否含 schema 变更、
      是否含底座升级）
- [ ] ECS 磁盘余量 ≥ 20%（镜像重建会临时占空间）
- [ ] 已确认低峰窗口（`docker compose up -d --build` 期间 Console 短暂不可用，
      运行中的 agent 任务会随网关重启中断——见 §5「中断语义」）
- [ ] `.env` 无待生效改动（发布不改 `.env`；改密钥是独立操作，见 DEPLOY.md §7）

## 5. 注意事项

1. **中断语义**：发布 = 网关容器重启，进行中的 agent run 会中断；任务池里的
   task 不丢（PG 持久化），但执行中的 loop 会停在 error，需要已识别的人或 Agent
   在任务详情的交付验收区打回重跑。低频使用场景可直接发；高频场景约窗口。
2. **绝不手动改容器内代码**：所有修复以新 tag 形式回到 AMAgentFleet。
3. **密钥只在 `.env`**：任何「临时调试」不要把 key 写进 compose 覆盖或镜像。
4. **`.env` 变更与发版解耦**：轮转密钥 = 改 `.env` →
   `docker compose up -d`（PG 密码轮换例外，见 DEPLOY.md §6.3）。
5. **gateway 镜像的 tag 漂移**：compose 里写的是 `image: am-fleet-gateway:<tag>`，
   换底座版本后旧 tag 镜像仍在 ECS 上，确认新版本稳定两周后可手动
   `docker image prune` 清理。
6. 首次发布、2-ECS 拆分、RDS 切换、内网离线构建四类特殊场景，一律查
   [DEPLOY.md](DEPLOY.md) 对应章节，不在本文件重复。

## 6. 联系

发布异常时收集：`docker compose ps` 输出、`docker compose logs --tail=200
gateway control-plane`、目标 tag 号，找开发团队排查。
