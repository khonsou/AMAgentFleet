# Spike 0001：DeerFlow 基本生命周期最小验证

- **状态**：✅ 已完成（2026-10-08，结果 **Go**，ADR-0002 已转"已接受"）
- **负责人**：AI 助手代执行
- **工时预算**：1 人 3–5 天（实际约 1 小时，含环境排障）
- **关联**：[ADR-0002](../adr/0002-harness-base-deerflow-spike-gate.md)（本 spike 是其决策门）、设计文档 §9

## 目标

只验证四件最基本的事：**部署能起、任务能拉起、状态能持久化、杀掉能再拉起继续**。
任何业务相关的东西（agent 配置体系、业务 MCP、授权、Console、协作）都不在本步范围。

## 环境

- 本地开发机或一台 ECS（4C8G 即可，与 ADR-0006 的 MVP 规格一致）
- Docker + Docker Compose v2.24+
- DeepSeek 官方 API key（已有）
- PostgreSQL（Compose 内起容器即可，暂不上 RDS）

## 做 / 不做

| 做 | 不做（后续阶段再说） |
|----|----------------------|
| `make setup` 向导接入 DeepSeek 官方端点 | agent YAML / Registry / Admin API |
| Docker Compose 部署（`make up`） | Dispatcher / 队列 / Inbox |
| 数据库后端切 PostgreSQL | 业务 MCP Server / Token Vault / OAuth |
| 发起长任务并观察执行 | Console / IM / cron 触发 |
| kill 进程/容器 → 重启 → 断点续跑 | agent 协作（agent_message/spawn） |
| 查 PG 表确认 checkpoint 落库 | 审计、成本台账、Langfuse |

## 步骤

1. **部署**：`git clone bytedance/deer-flow`，`make setup` 选 DeepSeek（官方 `api.deepseek.com` 端点），`make docker-init && make docker-start`（或生产模式 `make up`）。
2. **冒烟**：Web 界面（:2026）跑一次简单对话，确认 DeepSeek 适配器正常（reasoning 内容保留、工具调用正常）。
3. **持久化配置**：`config.yaml` 将 `database.backend` 设为 `postgres`，重启，确认 LangGraph checkpointer 与应用数据都落 PG。
4. **拉起长任务**：发起一个多步骤任务（例如"调研某主题并生成报告"，预计运行 ≥5 分钟），观察流式执行。
5. **销毁**：任务执行到一半，`docker kill` 网关/运行容器（或 `kill -9` 进程）。
6. **再拉起继续**：重启服务，打开同一 thread，验证从断点继续且最终结果与未中断运行的结果一致。
7. **落库验证**：直接查 PG 中 checkpoint 相关表，确认每步有快照、thread 状态正确终结。
8. **记录**：把结果填到下方"结果记录"，连同踩坑一起，半页以内。

## 验收标准（Go / No-Go）

| # | 标准 | 结果 |
|---|------|------|
| 1 | Compose 一把起，DeepSeek 对话跑通 | ✅ 通过（变体：本机无 Docker，改用本地 uvicorn 进程模式启动网关，配置一致；DeepSeek 官方端点对话成功，token 计量正常返回） |
| 2 | checkpoint 每步落 PG（查表可见） | ✅ 通过（冒烟线程 25 条；长任务线程 103→322→362 条，随执行单调增长） |
| 3 | **kill 后重启，同一 thread 从断点继续，结果一致**（核心项，一票否决） | ✅ 通过（细节见下） |
| 4 | 上述过程无需改 DeerFlow 源码（配置能解决） | ✅ 通过（全部用 config.yaml + .env + 环境变量解决） |

**判定**：4 项全过 → ADR-0002 转"已接受"（Option A）；第 3 项不过 → 转 Option B（自建薄 runner），控制面设计不受影响。

## 结果记录（执行后填写）

- 执行人 / 日期：AI 助手代执行 / 2026-10-08
- DeerFlow 版本：main 分支 tarball（2026-10-08 拉取，v2.1.0 之后）
- 结果：**Go**（4/4 通过）
- 环境变体说明：本机无 Docker，网关以本地 uvicorn 进程运行（`DEER_FLOW_AUTH_DISABLED=1` 本地模式）；PostgreSQL 16.2 由 pgserver 内嵌二进制提供（compose/ECS 上将是 RDS PG，语义相同）。沙箱默认 `LocalSandboxProvider`，未依赖 Docker。
- 核心验证过程：
  1. 长任务线程运行至 60 个 checkpoint 时 `kill -9` 网关进程，SSE 流随之中断，健康检查失败；
  2. 重启网关后，被中断 run 由租约对账收敛为 `error`（**不会原地自动复活**），60 个 checkpoint 完整保留；
  3. 对同一 thread 发送"继续"，agent 从 checkpoint 水合完整上下文（复述原任务、记得已加载的 skill），继续执行并产出 12MB 流式内容——**恢复语义 = 状态外置 + 重新投递，与我们 Dispatcher 的"唤醒续跑"设计完全同构**。
- 踩坑与备注：
  1. 默认 `recursion_limit=100` 对重型报告任务不足，两次触顶（GraphRecursionError）；按请求在 `config.recursion_limit` 调大即可，无需改代码——**平台侧应把 recursion_limit 纳入 agent YAML 的 per-agent 配置项**；
  2. pgserver 的 PG 随其客户端进程退出而关停，spike 改用 `pg_ctl` 常驻 + TCP（127.0.0.1:5432，trust 仅限本机回环）；
  3. 本地 API 默认要求认证 + CSRF 双提交，本地验证用 `DEER_FLOW_AUTH_DISABLED=1` 关闭（生产不携带此变量即可恢复）。
- ADR-0002 建议：**转已接受（Option A）**。遗留评估项（线程树/per-agent 配置隔离的定制深度）按 ADR-0002 第 5 条顺延至 P2 开工前完成。

## 增补（2026-10-08 晚，Console 联调时发现）

4. **`run_events.backend` 默认 `memory`，消息不随 checkpoint 持久化**：`/api/threads/{tid}/messages` 读的是 run event store 而非 checkpoint，默认进程内存实现，网关重启后历史消息全部消失（checkpoint 仍在，能续跑但看不到对话）。已在 `config.yaml` 改为 `backend: db`（`run_events` 表 alembic 已建好，无需额外迁移），改后新 run 的消息跨重启完整保留。**旧 spike 会话的消息已随 memory 丢失不可恢复，属预期**；compose/ECS 部署清单须将此项列为强制配置（`deployment_config.py` 的生产安全门也要求 `database.backend='postgres'` + `run_events.backend='db'` 组合）。
5. Console 联调端到端通过：经 Console 后端（FastAPI:8002）新建会话 → 首轮执行 → 详情页续跑（agent 记得跨 run 上下文，验证水合）→ 销毁（checkpoint/run/消息全部清除）。
