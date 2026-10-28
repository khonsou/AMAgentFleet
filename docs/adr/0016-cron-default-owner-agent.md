# ADR-0016：cron 默认直派所属 Agent，指挥官编排必须显式配置

- **状态**：已接受
- **日期**：2026-10-28
- **取代**：ADR-0010 第 4 条中“所有 cron 均由舰队指挥官认领”；ADR-0012 第 3 条中的“指挥官 claim”相应指“选定执行者 claim”，其原子性要求不变

## 背景

Agent YAML 是定义，Task 是业务实例，Loop 是执行单元，Thread 才是 Agent 的运行实例。ADR-0010 将所有 cron 默认交给舰队指挥官，导致配置在对账 Agent 定义上的触发器首先创建指挥官实例，而非对账 Agent 实例；定义归属与实例执行者不一致，也无法保证专精 Agent 必然执行。

## 决定

1. `agents/*.yaml` 根级 `triggers.cron` 到点后仍创建 Fleet Task 和 Loop；**未配置编排者时，由 YAML 所属 Agent 直接认领并创建 Thread**。
2. 单条 cron 可显式设置 `orchestrator: <agent-name>`，此时由指定 Agent（通常为舰队指挥官）认领并决定后续编排。全局 `FLEET_COMMANDER_AGENT` 仅作为显式部署级默认编排者，不再内置 `fleet-cmd-01` 默认值；单条 YAML 配置优先。
3. Task／Loop／Thread、交付捕获、统一验收与审计模型不变。`cron.fired.agent_name` 继续记录触发器所属 Agent，`claimed_by` 记录实际认领者。
4. 已有运行中 Task／Loop 不回写；新触发按本规则执行。需要保留旧行为的 cron 必须显式配置 `orchestrator: fleet-cmd-01` 或设置全局环境变量。

## 后果

- 正面：定时配置与执行实例归属一致；专精任务不再多一层不必要的指挥官调用；复杂编排仍有统一入口。
- 负面/约束：未显式配置编排者的既有 cron 行为会改变；显式编排会增加一次指挥官执行及其模型成本。Task／Loop 强制验收不变。

## 已考虑的备选

- **所有 cron 继续由指挥官认领**：否决，混淆定义与实例，且不能保证所属 Agent 执行。
- **直接使用 DeerFlow scheduled-tasks**：否决，会绕开 Fleet Task／Loop 与验收模型，需另建同步适配层。
- **新增独立 Schedule 实体**：暂不需要；当前 YAML cron 已能表达触发归属，避免增加概念。
