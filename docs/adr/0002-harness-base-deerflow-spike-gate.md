# ADR-0002：Harness 以 DeerFlow 2.0 为底座（设 spike 决策门），备选自建薄 Runner

- **状态**：已接受（2026-10-08，Spike 0001 四项验收全过：部署、拉起、PG 持久化、kill 后断点续跑）
- **日期**：2026-10-08

## 背景

LangGraph 库为 MIT，但官方服务层 `langgraph-api` 是 Elastic License 2.0，自托管生产需商业授权且要求外连 license 验证——服务层（队列、持久化、流式、崩溃恢复）本需自写。调研发现字节跳动 DeerFlow 2.0（MIT，83.5k star，523 贡献者，发布节奏稳定）恰好是生产化的 LangGraph 服务层：Postgres checkpointer、多 worker 租约崩溃接管、IM 频道、cron、skills、Langfuse 追踪，且对 DeepSeek 官方端点一等适配。

## 决定

1. W1 执行 spike（范围已于 2026-10-08 收窄为最小验证，清单见 [../spikes/0001-deerflow-basic-lifecycle.md](../spikes/0001-deerflow-basic-lifecycle.md)）：**部署能起、任务能拉起、状态持久化落 PG、kill 后能断点续跑**——四项全过即采纳 Option A。
2. 通过则以 **Option A：DeerFlow 当产品部署**——能配置不改代码，自研仅碰 `agent_message`/`agent_spawn` 与 per-agent 隔离边缘；fork 锁版本，按需 cherry-pick 上游。
3. 不通过则退 **Option B：自建薄 runner**（LangGraph MIT 库 + FastAPI，核心约 200–400 行）。
4. 两路线共用同一套控制面代码（Registry/Dispatcher/Vault/Gateway/Console），风险隔离在 harness 层。
5. 线程树/per-agent 配置隔离的**定制深度评估不在本 spike 范围**，顺延至 P2 阶段开工前完成（届时若发现定制不可行，仍可退 Option B，损失为 P2 前的工作量）。

## 后果

- 正面：省 2–3 周服务层自研；DeepSeek 适配、IM、cron 现成；决策门把选型风险限制在 3 天。
- 负面：DeerFlow 代码体量大（约 35MB），fork 有跟进成本；若其架构演进与定制冲突，退回 Option B 的切换成本已在设计内。

## 已考虑的备选

- 直接购买 LangGraph Platform/LangSmith 商业授权：否决，增加供应商与预算依赖，且 Elastic License 自托管有外连要求。
- 以 openshift-online/agent-control-plane（ACP）为底座：否决，见 ADR-0006 与 ADR-0007；仅作数据模型/凭证隔离蓝本。
- OpenAI Agents SDK + Temporal/DBOS：备选保留。SDK 模型中立可接 DeepSeek，但 durable 引擎对 MVP 过重；任务跨天场景出现时再评估。
