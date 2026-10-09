# Spike 0003：任务池（task/loop）全链路落地与冷启动验收

- **状态**：✅ 已完成（2026-10-09 晚，ADR-0010 落地验收 **Go**）
- **工时**：约 3 小时（含 Console 三处交互与冷启动回归）
- **关联**：[ADR-0010](../adr/0010-task-loop-pool-mandatory-acceptance.md)、[ADR-0004](../adr/0004-multi-agent-thread-tree-summary-only.md)、[Spike 0002](0002-adrs0008-door-validation.md)

## 验收范围

ADR-0010 定义的任务池模型：task（验收单元）/ loop（执行单元）分离、竞争领取（≤3）、
强制验收、打回同 thread 续跑、人机同构交付、runner 工具 agent_spawn/agent_message。

## 验收项与结果（冷启动后实测，2026-10-09 18:3x）

| # | 验收项 | 结果 |
|---|--------|------|
| R1 | 冷启动数据存活（PG→网关→Console→CP 全停重起） | ✅ task 39 验收态完好、8 agents、历史 loops 无损 |
| R2 | 池闭环：建 task(open) → 两个 agent 抢单 → 并行交付 → 强制验收采纳 | ✅ task#48 双 delivered，采纳后 done |
| R3 | 打回：退回意见 → **同 thread 水合续跑** → v2 交付（自动补充了要求的数学定义） | ✅ loop v1→v2，comments 落库，pool→review |
| R4 | 指挥官自主 spawn：agent_spawn 工具（冷启动后）→ 子任务入池 → worker 执行交付 | ✅ 子任务 review，parent 经 caller_thread_id 挂树 |
| R5 | 周边存活：scheduler 对账（unchanged 1）、MCP 注册表（http）、Inbox 循环 | ✅ |
| R6 | Console 数据源：全量任务接口、首页、task 详情、验收台 | ✅ 200 |

补充功能验证（落地过程中实测）：
- loop 上限：第 4 个领取 409（`已达 3 个 loop 上限`）
- 人交付：`POST /loops/{id}/deliver` 与 agent 产出同构比较（task#34 人机双交付并排）
- 关闭作废：排队任务（#40/#41/#44）可终止，`task.closed` 记审计
- 拒环：dispatcher 沿 parent 链查重，闭环即拒（早前 task#26 `collaboration cycle detected`）
- 迟到结果守卫：task 已验收后到达的执行结果被丢弃（`loop.late_result_dropped`）
- human loop 不被 dispatcher 执行（return 后等人修正重交付，不自动跑）

## 关键实现事实

- **dispatcher 池化**：消费 loop 非 task（`fleet_task_loops` 队列为内存 Queue + 懒启动
  consumer；running 超时回收重投递，幂等）。单消费者串行为已知项，生产扩 runner 池。
- **runner 工具零改 DeerFlow 源码**：`fleet_tools.py` 经 config.yaml
  `use: fleet_tools:agent_spawn`（PYTHONPATH 挂载）注册为 `group: fleet`；
  StructuredTool 需**展平函数签名**（args_schema 展开字段绑定，config 注入需
  RunnableConfig 注解参数）——坑已踩平。
- **父任务挂树**：agent_spawn 传 `caller_thread_id`（LangGraph configurable），
  control-plane 反解 loop → task 作 parent。
- **agent 领取同步**：task.agent_name 从 unassigned 更新为领取者，看板无悬空态。

## 冷启动手册（实测有效）

```bash
cd /Users/linan/Documents/Kimi/Workspaces/AMAgentFleet
S=spike/pgenv/lib/python3.12/site-packages/pgserver/pginstall/bin
"$S/pg_ctl" -D spike/pgdata -l spike/pgdata/server.log \
  -o "-p 5432 -h 127.0.0.1 -k $PWD/spike/pgdata" -w start          # 1. PG
cd spike/deer-flow/backend && PYTHONPATH=.:/Users/linan/Documents/Kimi/Workspaces/AMAgentFleet \
  DEER_FLOW_PROJECT_ROOT=$OLDPWD/spike/deer-flow \
  DEER_FLOW_HOME=$OLDPWD/spike/deer-flow/backend/.deer-flow \
  DEER_FLOW_AUTH_DISABLED=1 uv run --no-sync uvicorn app.gateway.app:app --port 8001 &  # 2. 网关
cd console/backend && .venv/bin/python -m uvicorn main:app --port 8002 &              # 3. Console
cd ../../control-plane && ../console/backend/.venv/bin/python -m uvicorn app.main:app --port 8003 &  # 4. CP
cd ../console/frontend && npx vite --port 5173                                          # 5. 前端
```

**启动后自检清单（每步一个命令）**：

```bash
curl -s localhost:8001/api/agents | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d['agents']),'agents')"   # 网关 agent 数
curl -s -X POST localhost:8003/api/mcp-servers/render   # 期望 {"managed":1,...}（内置 http 恒在；≠1 说明渲染链路断了）
curl -s -X POST localhost:8003/api/scheduler/sync       # 期望 200 {"fired":[],"retired_gateway_crons":0}
```

⚠️ **已知坑**：`spike/deer-flow/config.yaml` 的 `tool_groups:` 必须含
`- name: fleet`（agent_spawn/agent_message 所在组）。组声明丢失的症状：
指挥官"找不到可用 worker"单兵作战（对话里 0 次 agent_spawn 调用）。
协作链路验证：派一个小任务给 fleet-cmd-01 要求 agent_spawn 给
fleet-collect-01，子任务应出现在任务列表且独立交付。

## 2026-10-09 夜增补（人机统一 + GUI 收口）

落地过程中追加的交互与修复（全部实测通过）：

- **人机认领统一**：`claim_loop` 任何认领（人/指派/指挥官）都同步写
  `task.agent_name`（人 → `'human'`），拒环排除 human/unassigned，
  存量未认领任务已迁移。根治了"人认领一条路线、agent 认领另一条"
  导致的双路线 bug（认领后任务卡不从未分配池消失等）。
- **人 loop 不计 3-loop 上限**：拒绝环只计 agent loop，人认领不设限
  （不消耗 token；ADR-0010 第 2 条已同步修订）。
- **人交付框**：`HumanDeliverBox` 回复中贴飞书文档链接即可进入审核，
  与 agent 产物同构进验收台；agent 交付的 artifacts 链接自动提取进
  deliverable（artifacts 是 path 段路由 `/artifacts/mnt/...`，Console
  代理 `/api/console/threads/{tid}/artifacts/{path:path}`，链接前缀
  `FLEET_ARTIFACT_LINK_BASE` 可配）。
- **统一认领 UI**：任务卡单一"指派/认领"下拉（🧑 我（人工）/ 🤖 各
  agent）+ 单一按钮，人和 agent 一视同仁。
- **交付/评论文本 URL 自动变链接**（`LinkifyText`）。
- **会话详情页就地验收条**：详情页可直接采纳/打回（review-context
  反查接口），不用回到 fleet 看版。
- **四标签筛选视图**：待分配 / 执行中 / 待确认 / 已验收，默认选中
  待分配+待确认；计数从全量算（修复过选中态计数 bug）。
- **批量清理滞留任务**：30 个 open 滞留任务关闭（有交付入 review、
  无交付归档），看板从 已验收44/执行中33 收敛到 已验收75/执行中2。
- **scheduler sync 返回模型修复**：`scheduler.sync()` 返回
  `{fired: [...], retired_gateway_crons: n}`，端点注解放宽为
  `dict[str, Any]`，修复 500（ResponseValidationError）。
- **第二轮滞留清理**：4 个回归期残留（task 82 已交付但 task 状态
  未同步、72/74 空 claimed loop、80 无人认领），按同规则修复
  （有交付 → review，无交付 → 关闭），看板收敛到 待确认1 / 已验收83。
- **标题统一「内容优先、编号低调跟随」**（四页面同规则）：Fleet
  看板 Loop 流面板、Task 详情、会话详情（取首条人类指令）、Agent
  详情任务卡，均以任务内容为主标题，loop/task/thread 编号降级为
  灰色小字后缀；卡片一行截断预览，详情页完整展示。
- **交付内容高亮**：Task 详情 loops 区与 Fleet 验收台的每份
  deliverable 改为绿框高亮卡片（`OUTPUT / 交付内容` 标签 + 绿色
  LED + 13px 深色正文，验收台去 line-clamp-3 看全文）；会话详情页
  agent 回复气泡同步升级为绿框深字（human 指令保持 signal 蓝框），
  信息层级明确为 交付 > 输入 > 元信息。

- **MCP tab 删除 + http 工具平台内置**：`http`（通用 HTTP 工具，egress
  并集白名单的载体）从注册表数据改为 `mcpservers.BUILTIN_HTTP`——渲染恒在、
  `DELETE/POST/PUT /api/mcp-servers/http` 一律 409（附中文提示：接入业务
  系统请在 agent YAML 声明 egress）。修复 21:14 误删 http 导致
  `extensions_config.json` 清空、4 个声明 egress 的 agent 失能的事故。
  Console 前端 MCP tab 整体删除（导航/Rack、`/mcp` 路由、`Mcp.tsx`、
  api 封装），tsc 零残留；control-plane 注册 CRUD 保留（未来真实业务
  MCP server 用）。审计：`mcp.removed` → `mcp.rendered` 链路完好。
- **agent 自主接入能力实测确认**（task 85，2026-10-09 21:30）：注册表
  为空 + http 内置渲染下，smoke-test-register 仅凭自身 YAML
  （`mcp_plugins: [http]` + `egress: timeline.angrymiao.com`）真实读通
  timeline：探测出 SPA 子路径与 API 表、读到 7 看板 469 卡元数据、
  对 401 受阻部分如实汇报不编造。结论：接入业务系统是 agent 能力，
  不需要任何平台侧 MCP 注册条目——用户删注册表的理由成立（但删除
  时点 http 尚由注册表管理，确会造成失能；内置化后删除才无影响）。
- **交付截断 bug 修复**：`_extract_preview` 默认 limit=200 把 agent
  自动捕获的 deliverable 截到 200 字符（task 85 断在句中），放宽为
  3900（`deliver_loop` 入库上限 4000，留产物链接余量）；已从对话回填
  loop 128 完整交付（1666 字符）。
- **销毁语义修复（僵尸 agent 根治）**：`remove_agent` 原为"删 YAML +
  删网关配置但注册表行只标 terminated"，造成销毁后行尸走肉——指派 409
  「start it before dispatching」、设置页 404「YAML not found」、
  点拉起还会假复活（状态回 registered 但配置已无）。修复：销毁=真删
  （注册表行一并 DELETE，与前端确认框「元数据将被清除」一致；
  fleet_tasks.agent_name 软引用不受影响）；`start` 端点加 YAML 存在
  校验，配置已销毁返回 404 提示重新部署。重建 fleet-cmd-01（v2）并
  实测：任务 87 派发→指挥官执行→完整交付（1574 字符，凭据缺失如实
  汇报 + 放行条件清单）。顺手删除遗留 `control-plane/agents/x.yaml`。
- **fleet 工具组缺失修复（协作链路断因）**：`spike/deer-flow/config.yaml`
  的 `tool_groups:` 声明段缺 `- name: fleet`，agent_spawn/agent_message
  不在 agent 工具列表——舰队指挥官查无派活工具，去文件系统找 worker
  未果后单兵作战（task 87 全程 0 次 spawn 调用）。补声明 + 重启网关后
  实测：task 88 指挥官成功 spawn task 89 给 fleet-collect-01，worker
  独立交付真实数据（7 看板 469 卡清单）。
- **挂树反解失效根治（#9 关闭）**：双层根因——①dispatcher 在跑完后
  才写 loop.thread_id，指挥官 run 中途 spawn 时反解查不到父 loop；
  修复为起跑前预生成 uuid 写库并传给网关 config。②`fleet_tools` 的
  config 参数注解是 `RunnableConfig | None`，langchain_core 1.4.9 的
  `_get_runnable_config_param` 用 `type_ is RunnableConfig` 严判，
  Union 类型不命中 → 注入静默跳过（config=None，caller=unknown-agent）。
  改为裸 `RunnableConfig`（默认 None）后注入恢复。实测 task 100 spawn
  → task 101 `parent_task_id=100` 挂树成功；历史 88/89 已手工补挂。

## 凭据原则（2026-10-09 定案）

**业务系统凭据 = 用户在对话里直接告诉 agent，平台不做任何秘密配置存储。**
理由（用户定调）：agent 的配置必须简单可见易懂，不接受任何不可见、
不可理解的秘密配置。密码随任务指令/IM 对话进入 agent 会话，对话与
审计留痕、可见可回溯；agent soul 约束「凭据只用于本次会话调用，不写
入任何文件」。已验证可行：task 87 人工提供 timeline 密码（测试凭据，已略，不入库），
指挥官换 token 后读穿 27 张卡明细。机器级高敏凭据走 P4 Token Vault
（Gateway 侧签发短期令牌，不注入 runner），与「人告诉 agent」通道并存。
~~MVP secrets 过渡方案~~（agent YAML credentials + secrets 目录注入）
**明确否决**，与可见性原则冲突。

## 遗留（2026-10-09 晚更新：前五项已闭环）

~~1. dispatcher 多消费者~~ ✅ 已落地：`FLEET_DISPATCHER_WORKERS`（默认 3）并行消费，
   实测两任务并行交付 6s（串行基线 40s+）。
~~2. cron 池化~~ ✅ 已落地：croniter 自算触发点，建池任务由舰队指挥官认领
   （`FLEET_COMMANDER_AGENT`，默认 fleet-cmd-01）；网关侧 fleet:* cron 已退役防双触发；
   首见基线不补投错过的历史（实测恰好一次触发）。注意：网关 `scheduler.enabled`
   仍可保留（非 fleet 任务），fleet cron 全由 control-plane 管理。
~~5. 任务池 GUI~~ ✅ 已落地：未认领池横向滚动货架视图（与进行中 loops 分区），
   统一认领/指派下拉 + 人工认领可进入 task 详情并交付。

3. **验收人身份**：当前 author 默认为 'human'/'me'；接 OIDC（P4）后记真实操作人。
4. **review 态通知**：review 态任务无通知机制（可接 IM，等 §12 QA-1 IM 选型答复）。
6. **fork 锁版本**：等用户提供 GitHub fork 地址后锁定 DeerFlow 版本（QA-8）。
7. **规模化验证**：10 agent 并发验收场景未跑（下一步候选）。
~~8. 人交付后 task.status 未联动~~ ✅ 已修复（2026-10-10）：根因在
   `deliver_loop` 只推进 pool_status 不动 status；现统一 `status='success'`
   （人机同一条 deliver 路径，天然一致）。冒烟：task 102 人认领交付后
   status=success / pool=review。
~~9. spawn 子任务挂树偶发失效~~ ✅ 已根治（见增补「挂树反解失效根治」，
   双层根因：thread_id 写入时机 + RunnableConfig Union 注解注入静默跳过；
   task 100→101 挂树实测通过，历史 88/89 已补挂）。
