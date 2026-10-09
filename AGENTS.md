# AGENTS.md — 仓库约定（人类开发者与 AI 编码助手共用）

## 项目一句话

阿里云上的多 Agent 舰队平台：YAML 定义 agent，无状态 runner 按需水合执行，DeepSeek 为唯一模型底座，MCP 接入公司业务系统。设计真理在 [deepseek-agent-fleet-mvp-design.md](deepseek-agent-fleet-mvp-design.md)，决策历史在 [docs/adr/](docs/adr/)。

## 目录结构

```
README.md                          # 项目索引
AGENTS.md                          # 本文件
deepseek-agent-fleet-mvp-design.md # 总体设计文档（当前真理）
docs/adr/                          # 架构决策记录（不可变，推翻只能新增 ADR）
docs/adr/NNNN-title.md
docs/spikes/                       # 验证性实验（0001 已通过）
spike/deer-flow/                   # DeerFlow 底座（开发期快照，含 config.yaml/.env；生产用 fork tag am-fleet-base-v1，见 deploy/）
spike/pgdata/                      # 内嵌 PostgreSQL 数据目录（pgserver 提供二进制）
console/                           # Fleet Console v0（已可用）
console/backend/main.py            #   FastAPI:8002 —— 只读 PG + 转发网关:8001
console/frontend/                  #   React+Vite —— fleet 看板 + 会话详情页
console/scripts/dev.sh             #   一键拉起 PG→网关→后端→前端，退出全回收
```

P2 起逐步建立的目标结构（✅ = 已落地）：

```
control-plane/ ✅    # Registry / Dispatcher / Scheduler / Admin API (FastAPI :8003)
  app/main.py        #   Admin API + 后台循环（consumer/scheduler/inbox）
  app/registry.py    #   agents/ YAML ↔ agents_api ↔ fleet_agents
  app/dispatcher.py  #   任务队列（PG SKIP LOCKED；Redis Stream 为部署期替换）
  app/inbox.py       #   agent 持久 Inbox，攒批合并唤醒
  app/scheduler.py   #   YAML cron ↔ scheduled-tasks API 对账
agents/ ✅           # agent YAML 定义（版本化，每个 agent 一个文件，一等公民）
gateway/             # MCP Gateway：工具白名单校验、令牌网关侧注入、审计
vault/               # Token Vault：OAuth token 托管（KMS 加密）
skills/              # SKILL.md 能力包（同步 OSS）
deploy/ ✅           # 生产部署资产（运维入口 DEPLOY.md；compose + Dockerfile + .env.example；底座镜像按 khonsou/deer-flow tag am-fleet-base-v1 构建）
```

## 安全红线（任何代码不得违反）

1. **业务系统的 access token 只允许存在于 MCP Gateway 内存中**，不得进 runner 环境变量、checkpoint、日志、审计正文（ADR-0005）。审计只记 `args_digest`。
2. Runner 出口默认拒绝；新增任何外联目标必须改网络白名单并注明理由。
3. MCP 写操作工具上架前必须单独评审，默认只读优先。
4. 模型调用只走 OpenAI 兼容协议，禁止 DeepSeek 专有 SDK（ADR-0003）。
5. 密钥、refresh token 一律进 Vault（KMS 加密列），禁止硬编码、禁止进 git。

## 工程约定

- **架构决策**：涉及 ADR-0000 第 4 条所列变化时，先写新 ADR 再改代码；设计文档随最新 ADR 回改。
- **Agent YAML**：schema 变更必须向后兼容或升 `apiVersion`；运行中任务锁定旧版配置的语义不得破坏。
- **协作语义**：`agent_spawn` 返回值必须经摘要压缩（≤500 字硬上限）；禁止把子线程全量 transcript 注入父上下文（ADR-0004）。
- **预算**：任何 LLM 调用路径必须携带 `agent_id`/`thread_id` 标签并计量 token；不得绕过 `maxTokensPerRun` 熔断。
- **审计**：`audit_events` 只追加，不更新不删除。
- **依赖**：引入新核心依赖需 ADR；优先标准库与已在用的栈（Python/FastAPI、LangGraph 生态、React）。

## 常用命令

```bash
# Console 全栈（PG → 网关:8001 → Console 后端:8002 → 前端；Ctrl+C 全回收）
cd console && npm run dev

# Control Plane（Registry/Dispatcher/Inbox/Scheduler，:8003；前提：PG + 网关已起）
cd control-plane && ../console/backend/.venv/bin/python -m uvicorn app.main:app --port 8003
```

网关强制配置（spike 0002/0001 结论）：`database.backend='postgres'`、
`run_events.backend='db'`、`agents_api.enabled=true`、`scheduler.enabled=true`。

## 当前阶段提示

Spike 0001（DeerFlow 底座）与 [Spike 0002](../docs/spikes/0002-adrs0008-door-validation.md)（ADR-0008 开门验证）均已通过。P2 主体已落地（2026-10-09）：`control-plane/`（Registry + Admin API :8003 + Dispatcher/Inbox/Scheduler）+ `agents/`（YAML 一等公民仓库）+ Console 接入（Registry agents 区 + agent 详情页配置/对话一页合并）。遗留：P2 验收规模化测试（注册 10 agent、cron 准时、休眠/唤醒全链路）；fork DeerFlow 锁版本（QA-8）待有 GitHub 仓库地址后执行；队列 Redis Stream 化为部署期替换项。
