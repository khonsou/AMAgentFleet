# Agent Fleet Control Plane（P2）

组织级薄控制面：Registry + Admin API + Dispatcher + Inbox + Scheduler 薄层。
设计依据见仓库根目录设计文档 §9 P2、ADR-0004/0008、spike 0002 结论。

## 运行（开发机）

依赖与 Console 后端共用（fastapi/asyncpg/httpx/pyyaml）：

```bash
cd control-plane
../console/backend/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8003
```

前提：PG（spike pgserver :5432）与 DeerFlow 网关（:8001）已启动，
且网关 `agents_api.enabled=true`、`scheduler.enabled=true`（见 spike 0002）。

## 结构

| 文件 | 职责 |
|------|------|
| `app/main.py` | Admin API（:8003）：agents CRUD / dispatch / inbox / audit / scheduler sync |
| `app/db.py` | asyncpg 池 + 建表（fleet_agents / fleet_tasks / fleet_inbox / audit_events） |
| `app/registry.py` | `agents/` YAML ↔ 网关 agents_api 导入导出 ↔ fleet_agents 元数据 |
| `app/dispatcher.py` | 任务队列（PG SKIP LOCKED，Redis Stream 为部署期替换项）+ 派发 + 拒环/限深 |
| `app/inbox.py` | agent 持久 Inbox；pending 合并为唤醒任务，复用最近 thread 水合 |
| `app/scheduler.py` | YAML cron ↔ 网关 scheduled-tasks API 对账（不自建触发引擎） |
| `app/mcpservers.py` | MCP Server 注册表：secrets 文件托管（0600，只写不读回）→ 渲染 extensions_config.json → 网关 cache reset |
| `app/templates.py` | agent 模板（agents/templates/*.yaml，占位符渲染） |
| `app/pool.py` | 任务池（ADR-0010）：task=验收单元 / loop=执行单元，竞争领取 ≤3，强制验收，打回同 thread 续跑 |

## 任务池（ADR-0010，已落地）

- `POST /api/tasks` 建 task（open）；`assignee` = 人页面指派（同时建 loop 入队）
- `POST /api/tasks/{id}/loops` 领取（agent/human，竞争非独占，≤3）
- loop 执行完**交付自动捕获** → task 入 review；`POST /api/loops/{id}/deliver` 人交付（人机同构）
- `POST /api/loops/{id}/return` 打回（评论落库，agent loop 自动同 thread 续跑；human loop 等人修正）
- `POST /api/tasks/{id}/accept` 强制验收：采纳 → done，其余 loop 归档
- runner 工具（`fleet_tools.py`，config.yaml `group: fleet` 注册）：`agent_spawn` 派子任务入池（经 caller_thread_id 反解父 task）、`agent_message` 进目标 Inbox 攒批唤醒

## 关键语义

- **单一组织作用域**：所有网关调用单服务身份；fleet 全员可见（QA-5）。
- **唤醒续跑**：Inbox pending → 合并 → 同 thread 水合（spike 0001 语义）。
- **协作纪律**：线程树深度 ≤3，parent 链重复 agent 即拒环（ADR-0004）。
- **审计**：`audit_events` 只追加，args 只存 sha256 digest（AGENTS.md 安全红线）。
- **熔断/预算**：`maxTokensPerRun` 等 per-agent 护栏字段已落库（fleet_agents.budget），执行侧 enforcement 为 P3 定制点。
