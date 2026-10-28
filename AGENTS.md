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
spike/deer-flow/                   # DeerFlow 底座（开发期快照，含 config.yaml/.env；生产用 fork tag am-fleet-base-v2，见 deploy/）
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
deploy/ ✅           # 生产部署资产（运维入口 DEPLOY.md；compose + Dockerfile + .env.example；底座镜像按 khonsou/deer-flow tag am-fleet-base-v2 构建）
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

### DeerFlow 配置语义坑（已实测，改前先读）

- **agent YAML `skills:` 三态**（上游白名单语义，极易踩雷）：缺省/`null` = 可见
  全部已启用技能（含 agent 自己 `skill_manage` 沉淀的自定义技能）；`["a","b"]` =
  白名单；`[]` = **零技能**——agent 创建的技能永远不可见。fleet 仓库统一用
  `skills:`（null），禁止无审查地写 `[]`。
- **`skill_evolution.enabled`** 已开启（fork tag `am-fleet-base-v2`）：agent 获得
  `skill_manage` 工具，可在对话中沉淀持久化技能到 `skills/custom/`（静态扫描 +
  moderation 模型双闸，写完即刷新 system prompt、跨会话生效）。
- **`memory_enabled: true`**（deermem）：per-agent 持久化事实自动落盘
  `.deer-flow/.../agents/<name>/facts/*.md` 并注入后续会话。注意：自动提取
  无法被 agent soul 的「凭据不写文件」约束约束——对话中的敏感信息可能被提炼
  进 facts（存服务端数据卷，不进 git）。高敏场景 per-agent 关 `memory_enabled`。
- **网关 PYTHONPATH 必须含仓库根**：config 的 fleet 工具组 `use: fleet_tools:*`
  依赖它；dev.sh 与生产 compose 均已配置，手工重启网关时勿漏。
- **spike venv 的 editable 安装会记住绝对路径**：`spike/deer-flow/backend/.venv`
  里 `_editable_impl_deerflow_*.pth` 指向创建时的源码位置；源码目录被移动/删除后
  （如旧任务工作区被清理），运行中的网关表面正常、一跑就报
  `FileNotFoundError: [Errno 2]`（certifi 证书路径等运行时文件访问全断）。
  修复：在 backend 目录执行
  `uv pip install --python .venv/bin/python --no-deps -e ./packages/extension-api -e ./packages/harness`
  然后重启网关。控制台/控制面两个 venv 已检查无此问题。

## 常用命令

```bash
# Console 全栈（PG → 网关:8001 → Console 后端:8002 → Control Plane:8003 → 前端）
# console/frontend 下同一命令也是全栈入口；Ctrl+C 只回收本次启动的服务
cd console && npm run dev

# Control Plane（Registry/Dispatcher/Inbox/Scheduler，:8003；前提：PG + 网关已起）
cd control-plane && ../console/backend/.venv/bin/python -m uvicorn app.main:app --port 8003
```

### Console / 预览启动排障（2026-10-27 实测）

- 预览项目根目录固定为 `console/frontend/`；`npm run dev` 必须把 CLI host/port
  透传到 Vite，不能只启动前端，否则页面会出现 500 / Failed to fetch。
- 先看 `console/logs/dev.log`：没有本次启动记录，说明预览启动器未进入项目脚本，
  应从最新预览卡片重新启动；有记录再看 gateway / console-backend / control-plane
  对应日志。不要仅凭构建成功判断完整环境可运行。
- 本地健康检查必须绕过代理；Console 的 httpx 本机客户端固定 `trust_env=False`。
  `uv` 使用绝对路径发现，bash 用 `/bin/bash`，避免预览 PATH 与交互 shell 不一致。
- 停止信号直接交给 Vite PID；各后端只按本次写下的 PID 文件回收。禁止模糊
  `pkill -f` 杀进程；禁止验完后遗留手工服务。若端口仍被占，先用
  `lsof -nP -iTCP:<port> -sTCP:LISTEN` 确认归属再停止精确 PID。
- Python 服务没有热更新：改完 `control-plane/` 或 `console/backend/` 必须重启对应
  8003 / 8002；Vite 页面变新不代表后端已变新。验收至少覆盖首页 API 200、无
  Failed to fetch、最终 Output 和 Task → Loop → Thread 实际点击。

网关强制配置（spike 0002/0001 结论）：`database.backend='postgres'`、
`run_events.backend='db'`、`agents_api.enabled=true`、`scheduler.enabled=true`。

## 当前阶段提示

Spike 0001（DeerFlow 底座）与 [Spike 0002](../docs/spikes/0002-adrs0008-door-validation.md)（ADR-0008 开门验证）均已通过。P2 主体已落地（2026-10-09）：`control-plane/`（Registry + Admin API :8003 + Dispatcher/Inbox/Scheduler）+ `agents/`（YAML 一等公民仓库）+ Console 接入（Registry agents 区 + agent 详情页配置/对话一页合并）。遗留：P2 验收规模化测试（注册 10 agent、cron 准时、休眠/唤醒全链路）；fork DeerFlow 锁版本（QA-8）待有 GitHub 仓库地址后执行；队列 Redis Stream 化为部署期替换项。
