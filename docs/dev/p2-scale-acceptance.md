# P2 规模化验收测试计划

> 状态：待执行。范围对应 README「下一步」P2 遗留：注册 10 agent、cron 准时、
> 休眠/唤醒全链路。执行方式：独立测试 agent（`scale-*` 前缀）+ 脚本化驱动
> （可复用 `scripts/test-fleet-collaboration.py` 的 `call()/check()` 骨架）。
> 结论记录到本文档「执行记录」节，全绿后在 spike 0003 补一条验收结论。

## 前置

- 全栈在跑：PG :5432 / 网关 :8001 / Console 后端 :8002 / control-plane :8003
  （`scripts/dev.sh` 或等价手工进程）。
- 基线 agent 不动：`fleet-cmd-01`（cron 认领者，`FLEET_COMMANDER_AGENT`）、
  `fleet-collect-01`、`fleet-analyze-01` 保持 registered。
- 记录基线：`GET /api/agents` 快照、`audit_events` 最大 id（便于算增量）。
- 约束：所有测试 agent 命名 `scale-<nn>`；测试后必须清理（见「清理」节）。

## 用例 A：注册 10 个 agent（批量生命周期）

| 步骤 | 操作 | 预期判定 |
|------|------|----------|
| A1 | 脚本循环 POST `/api/agents`（YAML 由模板生成，name=scale-01..10，soul 各异、egress 留空） | 10 个 201；`fleet_agents` 行齐 |
| A2 | `GET /api/agents` | 10 个 status=registered、synced_at 新鲜、config_version=1 |
| A3 | 抽查 `GET :8001/api/agents/scale-05` | 网关侧 agent 存在且字段与 YAML 一致（Registry→网关同步链路） |
| A4 | 对 scale-01..03 各 dispatch 一条一句话任务 | 各自 loop 执行、交付、入 review（smoke） |
| A5 | PUT `/api/agents/scale-04` 改 soul/description | config_version+1，网关侧同步生效 |
| A6 | `DELETE /api/agents/scale-06` | 204；Registry 与网关两侧均消失（`GET` 404） |

通过标准：A1–A6 全 ✅，全程无 5xx；token 消耗记录在案。

## 用例 B：cron 准时（触发精度 + 恰好一次 + 重启续投）

机制口径（scheduler.py）：croniter 算「上一个应触发点」> `fleet_cron_fires.last_fired`
即投递；30s 扫描一轮；首次见到只记基线不补投；幂等键 `cron:<agent>:<i>:<occurrence>`；
投递为开放池任务并由舰队指挥官认领（`source='cron'`，audit `cron.fired`）。

| 步骤 | 操作 | 预期判定 |
|------|------|----------|
| B1 | 给 scale-07 注册带 `triggers.cron: [{cron: "*/2 * * * *", prompt: "[验收] cron 报时：回复当前分钟数"}]` 的 YAML | 首次 sync 只记基线、不补投（池里无历史任务） |
| B2 | 观察 ≥ 3 个触发周期（约 7–8 分钟） | 每周期恰好 1 个 `source='cron'` 任务；任务实际入队时刻 − 应触发点 ≤ 30s 扫描粒度 + 执行排队；指挥官认领并交付；audit 每周期 1 条 `cron.fired` |
| B3 | 周期 2 与 3 之间重启 control-plane | 不双触发（幂等键 + last_fired 落库）、不漏触发（重启后凭 last_fired 续投） |
| B4 | 交付内容核对 | 回复的分钟数与触发时刻一致（证明是定时拉起而非人工/随机） |
| B5 | 清理：改 YAML 摘除 cron 并重新部署 | sync 后不再产生新任务 |

已知边界（不判失败，记录在案）：cron 最小有效粒度受 30s 扫描限制，小于 1 分钟的
cron 表达式不建议验收使用；摘除后 `fleet_cron_fires` 行保留，若同名同序号 cron
再次挂载会按旧基线立即补投一次——测试后应删除该行（清理节含 SQL）。

## 用例 C：休眠 / 唤醒全链路

| 步骤 | 操作 | 预期判定 |
|------|------|----------|
| C1 | `POST /api/agents/scale-08/stop` | status=hibernated |
| C2 | hibernated 状态下 dispatch scale-08 | 409（`is hibernated; start it before dispatch`） |
| C3 | hibernated 状态下 `POST /api/agents/scale-08/inbox` 投递消息 | 被拒（dispatcher/入池校验），不产生 loop |
| C4 | hibernated 状态下 PUT 修改其 YAML | 成功（休眠期可改配置），config_version+1 |
| C5 | `POST /api/agents/scale-08/start` | status=registered；dispatch 恢复可用，新配置生效 |
| C6 | 连续投递 3 条 inbox 消息给 scale-09（registered） | flush_loop（5s）攒批为 1 个 task（idempotency `inbox:<agent>:<ids>`），3 条消息合并在一个 loop 的输入里执行 |
| C7 | 由 fleet-cmd-01 用 `agent_message` 工具给 scale-09 发消息 | 与 C6 同路径入池唤醒（A2A 唤醒面） |
| C8 | scale-09 被唤醒后交付 | 交付内容包含合并后的各条消息要点 |

通过标准：C1–C8 全 ✅；C2/C3 的 409 与 C5 的恢复是核心判定。

## 执行纪律与清理

1. 每用例结束即清理自己的 `scale-*` agent：`DELETE /api/agents/scale-<nn>`
   （网关侧同步删除，204）。
2. cron 测试后执行：
   `DELETE FROM fleet_cron_fires WHERE agent_name='scale-07';`
3. 测试产生的 task/loop 行保留可查（`scale-*`/`[验收]` 关键词可检索），
   review 态任务由执行人验收或 close，不留 open 干扰看板。
4. 全部用例完成后跑一次冷启动回归（`scripts/dev.sh` 全回收再拉起 + Console
   冒烟：发起→领取→交付→采纳），确认规模化测试未留隐性状态。
5. 结果回填本文档「执行记录」，并在 `docs/spikes/0003-task-pool-acceptance.md`
   补一条日期化验收结论；随后更新 README「下一步」勾掉 P2 规模化验收。

## 风险与备忘

- **成本**：10 agent + cron 多周期 + 多轮唤醒，deepseek-chat token 消耗预计
  数十万级（远低于各 agent dailyTokenCap 200 万），无需特殊限额。
- **并发**：dispatcher 3 worker 并行消费，B/C 用例与日常 agent 测试并行跑
  不冲突；如观察 loop 排队 > 5 分钟，先查 `fleet_task_loops` claimed/running
  滞留再判失败。
- **Flake 排查**：任一判定失败先查 `audit_events`（增量区间）与
  `console/logs/control-plane.log`，区分产品 bug 与环境问题，如实记录不做
  选择性重试。
