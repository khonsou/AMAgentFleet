# ADR-0001：Agent 即配置，Runner 无状态，状态外置到 Checkpointer

- **状态**：已接受
- **日期**：2026-10-08

## 背景

目标注册规模数千个 agent，但确认并发峰值约 10。若为每个注册 agent 常驻进程，95% 以上资源空转。同时业务要求支持"拉起、销毁、休眠、再次拉起继续任务"。Anthropic（subagent 即 YAML 配置 + supervisor 托管会话）与 OpenAI（harness 与 compute 分离、snapshot/rehydration）的实践一致指向同一模型。

## 决定

1. **Agent = 一份声明式 YAML 配置 + PG 中的状态指针**，不是常驻进程。配置含模型、skills、工具白名单、身份、触发器、预算，版本化管理。
2. **Runner 容器无状态**：领取任务后按 `agent_id` 拉配置、按 `thread_id` 从 Postgres checkpointer 水合状态，每步执行回写 checkpoint。
3. **休眠 = 释放容器**（状态已落库）；**唤醒 = 消息进 Inbox，新容器从断点续跑**。
4. 固定小池（10–12 个 runner 容器），MVP 不引入弹性伸缩组件。

## 后果

- 正面：注册数与并发数解耦，数千注册不增加待机成本；kill 容器不丢任务；扩容路径（KEDA/ACK）不影响架构。
- 负面/约束：冷启动约 1–2s（拉配置 + 水合），交互场景可接受；checkpoint 表成为关键资产，需备份与 TTL 清理策略；LangGraph checkpointer 需锁版本并跟踪安全公告（2025-11 曾披露 RCE 链，已修复）。

## 已考虑的备选

- 常驻 pod 每 agent 一个：否决，成本与运维随注册数线性增长。
- 函数计算 AgentRun（阿里云托管运行时）：备选保留。MVP 阶段选择自控数据平面（定制协作语义的需要）；若运维压力成为问题可再评估。
