# ADR-0012：控制面并发原子性与单例门闩

- **状态**：已接受
- **日期**：2026-10-15

## 背景

2026-10-15 多用户共享任务池场景的并发评审确认一组原子性缺陷，全部源于
check-then-act 与非原子多步写（评审过程见会话记录，决议 QA-13~16）：

1. `pool.claim_loop` / `dispatcher._run_loop` 的 `SELECT ... FOR UPDATE`
   未包显式事务（asyncpg 自动提交，锁随语句结束即释放），认领上限可被并发击穿。
2. inbox 唤醒：`create_task` 幂等回放（`ON CONFLICT` 命中）走早退路径且不补
   claim——休眠期间到达的消息在 agent 恢复后永久搁浅（task open、0 个 loop）。
3. cron 投递"建 task → 指挥官认领 → 记 last_fired"三步非原子，崩溃窗口内
   重启会重复认领、重复执行（两个 loop 是不同 thread，网关 busy 冲突不兜底）。
4. FAP 幂等中间件 check-then-act：并发同 `Idempotency-Key` 请求双双穿透执行
   （第三方 agent/Codex 的重试风暴是常态）。
5. 控制面隐含单例假设（内存队列、进程内定时循环）无任何门闩：滚动更新瞬间
   新旧容器并存、或误开第二实例，会 cron 双触发、任务双跑。
6. 重启无恢复扫描（claimed 态 loop 搁浅至人工 requeue）；reaper 窗口 30min
   ≈ httpx 超时 31min 且运行中无心跳，长任务被误回收；`_resume_comments`
   存进程内存，打回意见在网关拒收时丢失。
7. `return_loop` 无状态守卫（running 中可打回）；`deliver_loop` 无条件 UPDATE
   （迟到结果可复活已归档 loop）；`upsert_registry_row` 无条件重置
   `status='registered'`（stop 被部署动作静默解除）。

**措辞澄清（QA-13）**：ADR-0010"同一 task 最多 3 个并行 agent loop"的语义
明确为**终身累计 ≤3 个 agent loop**（含已交付/已归档；打回续跑复用原 loop、
version+1，不占额度；human loop 不计入）。维持现有实现，ADR-0010 原文措辞
以本条为准（ADR 不可变，澄清以新 ADR 承载）。

## 决定

1. **认领事务化**：`claim_loop` 整体包入 `conn.transaction()`，task 行
   `SELECT ... FOR UPDATE` 在事务内生效；`dispatcher._run_loop` 的
   状态检查 + 置 running 同样收进事务。并发认领不再击穿累计上限。
2. **认领者唯一**（QA-14）：`fleet_task_loops` 加
   `UNIQUE(task_id, claimant_type, claimant)`——同一人/同一 agent 对同一
   task 终身只能有一个 loop；重试/打回续跑走 requeue 原 loop，不新建。
   上线前清理存量重复行。
3. **cron 投递单事务**：建 task + 指挥官 claim + 记 `last_fired_at` 收进同一
   事务；幂等键 `cron:{name}:{i}:{occurrence}` 保留为兜底。
4. **inbox 搁浅修复**：幂等回放命中既有 task 时检查其活跃 loop，无则补
   claim 并入队；消息不再卡在 dispatched。
5. **FAP 幂等占位行**：写操作先 `INSERT` 占位行（`(key, actor)` 主键），
   冲突者回放已存响应或拒绝；执行完成后回填 response。24h 窗口不变。
6. **单例门闩**（QA-16）：控制面启动时 `pg_try_advisory_lock` 拿常驻锁；
   拿不到锁则 scheduler/inbox/dispatcher 后台循环不启动（API 照常服务），
   日志告警。部署维持 compose 单副本。
7. **恢复与心跳**：启动时扫描 claimed 态 loop 重新入队；dispatcher 执行
   期间周期更新 `loop.updated_at`（心跳）；reaper 窗口调整至显著大于
   run 超时（≥2×RUN_TIMEOUT）；`_resume_comments` 落库（loop 表加列），
   不再使用进程内存。
8. **状态守卫与验收权**（QA-15）：`return_loop` 仅 delivered 态可打回；
   `deliver_loop` 改条件 UPDATE（仅 claimed/running/returned 可交付）；
   验收动作（accept/return/close）强制 `author ∈ {task 创建者, admin}`，
   admin 通道用于接管离职者任务。
9. **注册状态保持**：`upsert_registry_row` 不再重置 status（hibernated
   保持 hibernated）；`apply_agent` 对同名 agent 加进程内锁串行化。

## 后果

- 正面：并发认领/重试/重启/滚动更新场景下不再出现双执行、任务搁浅、
  上限击穿；单例假设从"约定"变成"强制"；验收权有了硬约束。
- 负面/约束：单例门闩意味着控制面将来扩容必须走"队列 PG/Redis 化 +
  active-active"路线（届时新增 ADR 废本条第 6 项）；心跳写入增加少量
  DB 流量。
- 实施列入 P3c，先于 FAP 公网暴露。

## 已考虑的备选

- 队列即刻 PG SKIP LOCKED 化 / Redis Stream 化支持 active-active：否决——
  10 并发量级用不到；滚动更新双触发用门闩即可消除。保留为扩容路径。
- 认领上限改并行口径（只数进行中）：否决（QA-13）——维持累计口径。
- 验收权放开给"创建者所在组"：否决（QA-15）——仅发起者与 admin。
