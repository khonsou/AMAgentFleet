# ADR-0004：多 Agent 协作采用线程树 + 强制 Summary-Only 返回

- **状态**：已接受
- **日期**：2026-10-08

## 背景

现有"单 agent 多 skill"系统在实践中出现上下文溢出、记忆混乱。根因是一个上下文承载了过多职责。Anthropic 的 sidechain 实践（子 agent 全量 transcript 单独存档，只把摘要返回父上下文）证明：隔离 + 摘要传递是唯一可持续的多 agent 协作方式。

## 决定

1. **线程树（Thread Tree）**：`threads` 表带 `parent_thread_id`，协作形成树。
2. 两个内置工具对所有 agent 可用：
   - `agent_spawn(agent_name, task, context_hint)`：创建子线程，阻塞等待摘要；
   - `agent_message(agent_name, content)`：异步唤醒（进目标 agent 的 Inbox），不等结果。
3. **Summary-only 是平台强制**：子线程返回经压缩步骤（DeepSeek 摘要 + ≤500 字硬上限）；全量过程只存子线程自己的 checkpoint，审计沿树下钻。
4. **失控防护**：树深度上限 3；单协作链 token 总预算；Dispatcher 检测并拒绝 A→B→A 环。
5. 上下文纪律平台化：一 agent 一窄职责；机器触发一任务一 thread；skills 按需注入；checkpoint 自动压缩折叠 + TTL；`maxTokensPerRun` 熔断。

## 后果

- 正面：上下文边界由平台保证而非依赖 prompt 自觉；协作全程可审计；token 消耗有上限。
- 负面/约束：复杂编排（orchestrator agent 动态规划子任务）不在一期，用固定职责 + 线程树覆盖；摘要信息有损，子任务设计要保证"结论可摘要"。

## 已考虑的备选

- 共享全量上下文的多 agent 群聊（AutoGen 式）：否决，正是现有系统失败的根因。
- 共享记忆库（Caura 式跨 agent 记忆治理）：二期再评估，一期用线程树 + 摘要已够。
