# ADR-0007：不采用的项目及其理由（GitHub 生态调研结论）

- **状态**：已接受
- **日期**：2026-10-08

## 背景

为避免闭门造车，调研了 GitHub `agent-fleet` / `control-plane` topic 约 40 个项目及国内外生态（2026-10-08，含 GitHub API 实时数据）。设计文档 §3 有完整对比，本 ADR 固化"不采用"的结论及理由，防止日后重复评估。

## 决定

| 项目 | 结论 | 理由 |
|------|------|------|
| openshift-online/agent-control-plane (ACP) | **仅作设计蓝本，不作依赖** | 12 star、无正式 release、OpenShift 生态重绑定、面向 coding agent（LangGraph bridge 为 stub）。照搬其数据模型（Project/Agent/Session/Inbox/Credential/ScheduledSession）与凭证掩码设计 |
| coding fleet 类（amux、Warren、Multica、ruflo、Conductor、Superset 等） | **不采用** | 领域不符：面向代码/PR 与开发者单机，不面向业务系统与员工 OAuth。仅借鉴模式：worktree 隔离 ≈ thread 隔离、supervisor ≈ Dispatcher |
| Dify / FastGPT / DB-GPT | **不采用** | 低代码画布路线与 code-first 决策冲突（OpenAI 已弃用 AgentKit 画布）；每 agent YAML + 深度定制的需求下画布是障碍 |
| TradingAgents | **不作基建，作领域参考** | 110k star 但仅 1MB Python 的研究框架：无服务端、无持久化、无鉴权。其价值在财务 agent 角色拆分（分析师/辩论/交易员/风控），设计首批 agent 职责时参考 |
| Caura / Bernstein（记忆治理、可重放审计） | **二期再评估** | 一期用线程树 + 摘要返回 + 追加式审计已覆盖需求 |

## 后果

- 正面：选型边界清晰，未来有人提议"要不要试试 X"时先查本表。
- 约束：本表带时间戳，半年后的复评应重新拉取数据而非沿用本结论。

## 已考虑的备选

- 逐项目做 PoC 再定：否决，调研数据（star/release/部署需求/领域匹配度）已足以在纸面排除；仅有的真正候选（DeerFlow）已设 spike 决策门（ADR-0002）。
