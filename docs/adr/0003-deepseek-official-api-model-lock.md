# ADR-0003：模型锁定 DeepSeek 官方 API，接口层保持 OpenAI 兼容

- **状态**：已接受
- **日期**：2026-10-08

## 背景

业务要求 harness 锁定 DeepSeek（财务/销售场景已验证效果与成本）。公司已取得 DeepSeek 官方 API key，并确认 agent runtime 部署在阿里云机房即满足安全要求，无需自建 GPU 集群。

## 决定

1. 模型访问统一走 `api.deepseek.com`（OpenAI 兼容端点），模型名（`deepseek-chat` / `deepseek-reasoner`）在 agent YAML 中配置化。
2. 代码中**不允许出现 DeepSeek 专有 SDK 调用**，一律走 OpenAI 兼容协议，保留切换能力。
3. 启用 DeerFlow 的 per-model `request_admission` 限速防止触发限流；每个 agent 配 `maxTokensPerRun` 与 `dailyTokenCap` 双预算，超限熔断。
4. 成本归账：每次调用按 `agent_id`/`thread_id` 记录 token 用量进 `audit_events`，月度出账单。

## 后果

- 正面：零 GPU 运维；token 是主要成本项（月 ¥300–1,000 量级），预算熔断第一天就有。
- 负面/约束：供应商单点——由 OpenAI 兼容协议缓解（可切阿里云百炼 DashScope 或自托管 vLLM，配置切换不改代码）；API 限流靠队列削峰与全局限速兜底。

## 已考虑的备选

- 阿里云百炼 MaaS：备选保留（合规或价格变化时切换成本低）。
- 自建 GPU + vLLM：否决，与"不自建 GPU"的输入结论一致，且 10 并发规模下无成本优势。
