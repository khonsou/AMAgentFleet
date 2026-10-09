# ADR-0009：模型政策放宽为"provider 锁定 + per-agent profile 自由"

- **状态**：已接受
- **日期**：2026-10-09（QA-6 结论）

## 背景

ADR-0003 将模型锁定为 DeepSeek 官方 API，ADR-0008 第 3 条进一步提出"平台层禁用 per-agent `model` 覆盖"。2026-10-09 业务方明确（QA-6）：**agent 可以自由配置使用的模型**——平台不做单一模型强制，例如财务核对类 agent 可用 `deepseek-chat`，深度推理任务可换 `deepseek-reasoner`。

## 决定

1. 政策从"单模型锁定"放宽为**两层**：
   - **接入层不变**：模型一律走 `config.yaml` 中声明的 profile，协议保持 OpenAI 兼容（ADR-0003 的 DeepSeek 官方 API 为当前唯一 provider，成本模型 §10 不变）；
   - **agent 层放开**：per-agent `model` 字段开放配置（复用 DeerFlow agents_api 原生能力），可选范围 = 已声明 profile。
2. 新增模型（含非 DeepSeek 的 OpenAI 兼容端点）= 运维动作：改 `config.yaml` 加 profile 并重启网关，无需平台代码变更；成本台账按 profile 归集。
3. 本 ADR 细化 ADR-0003（provider/协议锁定维持），并取代 ADR-0008 第 3 条（禁用模型覆盖）的内容。

## 后果

- 正面：业务可按任务类型选模型（推理/长文/廉价闲聊），无需等平台排期；agents_api 的模型校验（未知 profile 422）天然防止乱填。
- 负面：成本预测粒度变细（需按 profile 归集，P4 成本台账覆盖）；新增非 DeepSeek provider 时须重审数据出域假设（QA-2 的"机房内即安全"不含第三方模型流量）。

## 已考虑的备选

- 维持单模型锁定（ADR-0008 第 3 条原案）：被 QA-6 推翻——业务已提出按任务选型的明确诉求。
- 完全放开任意 provider/任意端点：否决——绕开 profile 声明会让成本、限流（request_admission）与审计失控；新增端点必须过 config.yaml 运维口子。
