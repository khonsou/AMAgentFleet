# ADR-0008：per-agent 配置层复用 DeerFlow agents_api，Registry 降级为组织级包装

- **状态**：已接受（方向；附 P2 开门验证项，验证不过则回退备选）
- **日期**：2026-10-08

## 背景

原设计（§6.1、§9 P2）假设 DeerFlow 只有 `config.yaml` 部署层配置，per-agent 配置体系（模型、skills、记忆、定时、MCP 白名单）需完整自建。

2026-10-08 Console 联调期间对 spike 源码核实（`backend/app/gateway/routers/agents.py`、`scheduled_tasks.py`、`skills.py`、`memory.py`、`frontend/src/app/workspace/`）：DeerFlow 2.0 原生具备——

- **自定义 agent CRUD API**（`/api/agents`，默认关闭，`agents_api.enabled=true` 开启），per-agent 字段覆盖：模型覆盖（校验须为 `config.yaml` 已声明 profile）、`tool_groups` 工具白名单、`mcp_plugins` 插件选择、`skills` 技能白名单、`allowed_subagents` 子代理名单、`model_settings`（temperature/max_tokens）、thinking/reasoning_effort、`knowledge_scope`（RAGFlow）、`soul`（SOUL.md 人格护栏）、`memory_enabled`；
- 版本化导入导出格式 `deerflow.custom-agent` v1；
- skills / memory / scheduled-tasks 全套路由与 DB 表（`scheduled_tasks`/`scheduled_task_runs`/`scheduled_task_events`），以及官方 Next.js 前端对应页面（`workspace/agents`、`workspace/scheduled-tasks`、`workspace/capabilities`）。

即"agent 不是配置文件拉起的，配置页面原生存在"。

## 决定

1. **per-agent 配置引擎不自建**，复用 DeerFlow agents_api；P2 的 Agent Registry 降级为**组织级包装层**：业务角色/归属部门、成本归集维度、fleet 统一视图、YAML 版本化（`agents/` Git 仓库 ↔ agents_api 导入导出对账）。
2. 员工入口统一走自建 Console（及后续 OIDC 门户）；DeerFlow 官方前端**不作为**员工入口（单用户"我的工作台"视角，无 fleet 维度/成本/授权），仅作实现参考。
3. ADR-0003 模型锁定不变：平台层禁用 per-agent `model` 覆盖，或仅放行 `config.yaml` 中已声明的 DeepSeek profile。
4. **P2 开门验证项（第一天，半天）**：
   - `agents_api.enabled=true` 端到端 CRUD + agent 会话跑通；
   - user-scoped 语义（agent 定义按用户隔离、SOUL.md 存文件系统）与"公司统一 fleet"模型的映射方案；
   - `agents/` YAML Git 仓库与 agents_api 存储的主次关系（建议 YAML 为一等公民，API 导入为部署动作）；
   - DeerFlow 定时任务的用户级语义能否承载平台 cron + 投递绑定（IM/Webhook/agent 唤醒）。
5. 验证若发现语义冲突不可调和，回退备选 A（自建薄 Registry）；harness 底座决定（ADR-0002）不受影响。

## 后果

- 正面：P2 配置体系工程量显著下降（skills/记忆/定时/MCP 白名单现成）；导入导出格式直接支撑 YAML 版本化；官方 agents 页面可作字段设计蓝本。
- 负面：user-scoped 到 org-scoped 的适配层需自写；agents_api 默认关闭且有独立的启用/权限语义，需摸清其与 `DEER_FLOW_AUTH_DISABLED`/PAT 认证的交互。

## 已考虑的备选

- **A. 自建薄 Registry 配置引擎**（借 agents 表模型，不走 agents_api）：保留为回退项。
- B. 直接开放 DeerFlow 官方前端当员工入口：否决——无组织级 fleet 视图、成本归集、Token Vault 授权链路，且身份模型与我们 OIDC 不一致。
- C. 完全不用 agents_api，YAML 直接生成 config.yaml 重启生效：否决——每 agent 变更都要重启网关，无法支撑数千注册规模。
