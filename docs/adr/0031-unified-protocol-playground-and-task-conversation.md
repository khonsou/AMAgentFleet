# ADR-0031：统一协议客户端、Task 群聊投影与可替换 Playground 表现层

- **状态**：已接受
- **日期**：2026-10-31

## 背景

Fleet 已有覆盖完整操作面的 FAP，但 Console 页面仍存在私有 API 封装。新的 Playground 需要以团队已确认的 Diorama 3D 风格展示真实 Fleet，并把一个 Task 的多个 LOOP、Agent、subagent、评论与 OUTPUT 聚合为一场用户可理解的群聊。若另建 Playground 专用业务接口，会再次形成能力、鉴权与审计分叉。

## 决定

1. **一套协议，三类客户端**：FAP 继续作为唯一操作清单；Console GUI、Agent 与 Playground 共用 operation ID、参数、写确认、幂等和错误语义。身份与凭证按既有通道适配，不增加 Playground 特权，不让浏览器调用 Runner 专用 `/internal/ops/execute` 或接触系统 PAT。
2. **共享 Protocol Client**：Playground 只经共享协议客户端读写。写操作必须明确确认并携带幂等键；human-only 操作只经人类 GUI 表单，秘密不得进入 Agent Thread、日志或审计正文。
3. **Task 群聊是通用只读投影**：`tasks.conversation` 聚合 Task、全部 LOOP、参与者、Thread 消息、Task 评论与 OUTPUT，对所有客户端开放。它不是新业务实体，不创建 Thread，不改变 Task／LOOP 状态机或数据库结构。写入仍使用既有 `threads.send`、`tasks.comment`、`tasks.accept`、`loops.return` 等 operation。
4. **消息路由不制造隐式广播**：群聊输入默认发给当前选中的 Agent／LOOP；“全组”只写 Task comment，不并行触发多个收费 RUN。OUTPUT 操作始终定位其真实 LOOP。
5. **表现与协议解耦**：Playground Runtime 输出风格无关的 ViewModel；renderer 只实现 mount／update／focus／resize／dispose，不直接请求业务 API。本版只实现团队已 review 的 Diorama 3D，不引入第二套视觉风格。
6. **核心依赖**：Three.js 作为 Diorama 3D 渲染依赖，按路由按需加载；离开页面释放渲染器、监听器和 GPU 资源。既有 Console 页面与业务逻辑保持不变。

## 后果

- 正面：协议能力不再按页面分叉；多 LOOP Task 获得统一用户心智；视觉演进不触碰业务；写操作保留既有安全边界。
- 负面／约束：聚合投影必须处理跨 Thread 时间排序、长消息分页与轮询成本；Protocol Client 需要对 Console、Control Plane、Gateway 三种 surface 做契约测试。
- 安全：通用 GUI 执行适配必须沿用现有 DAO JWT 与受信服务转发，服务端重新校验 operation、写确认与 actor，不能信任浏览器传入的身份。

## 已考虑的备选

- **Playground 专用业务 API**：否决，会形成 GUI／Agent 能力分叉。
- **直接跳最新 Thread 代替 Task 群聊**：否决，会丢失竞争 LOOP、Task 评论与完整验收语义。
- **群聊消息广播到全部 LOOP**：否决，会隐式放大模型费用与副作用。
- **同时制作第二套视觉风格**：否决，本版严格保持团队已确认的 demo 视觉，仅保留渲染器替换边界。
