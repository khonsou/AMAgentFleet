# ADR-0049：Goal／Task／Loop／Graph 统一语义与 Check-First 节点契约

- **状态**：提议中
- **日期**：2026-11-05

## 背景

ADR-0010 已分离 Task（验收单元）与 Loop（执行单元），ADR-0042 又引入 Workspace.goal、ExecutionPlan DAG、Evidence 与 Capability。当前因此同时存在父子 Task Tree 和 ExecutionPlan DAG 两种编排表达：前者擅长谱系、摘要返回和父 Loop 唤醒，后者擅长数据依赖、并行与回退，但二者的语义边界尚未统一。

另一方面，Loop 已支持交付、打回和同 Thread 续跑，却不必然包含“先定义可失败检查，再执行、修复和停止”的闭环；`stop_conditions` 可保存也不等于已被执行。若只增加自然语言 Goal 或 reviewer Agent，会把“运行结束”继续误当“目标达成”。

## 决定

1. **Task 是 Goal 的可执行实例**：Goal 表达期望结果、约束、反目标与完成标准；Task 为其增加执行者、状态、预算、交付和验收。Goal 是逻辑契约，不新增独立 Goal 表；单节点工作可直接创建 Task，复杂 Goal 以 Workspace 保存根 Goal 并关联根 Task。
2. **统一层级为 `Goal → ExecutionPlan → Task／Capability Node → Loop → Run`**：Task 决定“做什么、怎样算完成”，Loop 是一次认领及其连续交付版本，Run 是 Loop 内一次执行或恢复，Thread 仍是 Loop 的隔离上下文；保持“一 Loop 一 Thread”和 summary-only 不变。
3. **DAG 与 Tree 分工，不互相冒充**：ExecutionPlan edges 是调度与数据依赖的唯一执行真相；`parent_task_id` 只表达谱系、审计、深度／环检测和父子唤醒。`agent_spawn` 语义改为向当前 Plan 动态增加 Task 节点及显式输入／输出引用；迁移期允许把既有 Task Tree 只读投影为 chain／join DAG，不另建第二套可写图。
4. **节点先声明契约再领取**：新编排节点必须声明 `goal`、`input`、`output_artifact`、`acceptance`、`stop_conditions`、`budget` 和 `on_failure`。确定性合并、去重、计算与校验走 Capability／代码；模型只承担判断、生成、评估和摘要。
5. **Check-first，且检查结果属于 Evidence**：自动执行不得把 reviewer 同意、无异常或模型自信单独视为通过。缺少机器可判定验收的 legacy Task 仍可按旧流程交付和人工验收，但不得计入 SZ-VSR，也不得触发自动验收。
6. **失败只修复失败节点**：检查失败后打回原 Loop、同 Thread 携带失败证据续跑；不得重写已通过节点。`RETRY／FALLBACK／SKIP／REPAIR／REPLAN／STOP` 映射到既有 fallback 与 `stop／replan／continue`，预算、权限或不可逆风险触发 STOP，禁止无停止条件扩张。
7. **停止条件必须可执行**：验收通过、预算耗尽、连续无新增有效 Evidence、能力阻塞或人工取消均可停止；自然语言可解释停止原因，但不能冒充机器门。Workspace 中已保存的 `stop_conditions` 必须参与实际执行判定。
8. **验收权与风险门分离**：保留 ADR-0015 的已识别人／Agent 同权验收；检查通过只产生 Evidence，不冒充验收 actor。低风险且确定性检查通过时可由已识别 Agent 自动验收，生产写入、迁移、删除、支付及最终 merge／deploy 仍由人授权。

## 后果

- **正面**：用户只需表达 Goal；静态 Plan 与指挥官动态拆题共享一套 DAG；Task Tree 继续提供审计和恢复能力；节点可独立测试、替换、并行和局部修复。
- **成本**：需要为 Task 增加兼容的契约投影、Evidence 引用和停止条件执行器，并维护迁移期 Tree→DAG 只读投影；为开放式研究工作编写有效 check 仍需业务 Owner 参与。
- **兼容边界**：不改变既有 Task／Loop 状态机、运行中 Loop 的配置快照、Agent YAML `apiVersion`、FAP major、凭据边界和 token 熔断；不引入新核心依赖。
- **实施顺序**：接受后先做语义映射与只读 DAG 投影，再上线节点契约和 check runner，随后接通动态 spawn 与停止条件，最后开放低风险自动验收；每一步分别回归单 Task、fan-out／join、局部修复、预算停止、高风险拒绝和旧 Task 兼容。
- **本稿边界**：仅提议语义与演进路径；未接受前不改设计文档“当前真理”，不宣称上述统一已完成。

## 已考虑的备选

- **直接令 Task 与 Goal 完全同义**：否决，会丢失执行、状态、交付与验收语义；采用“Task 是 Goal 实例”而非两个平行实体。
- **继续以父子 Task Tree 充当完整 Graph**：否决，谱系不能完整表达数据依赖、无关节点并行和显式重路由。
- **只增加 reviewer Agent 作为质量门**：否决，模型意见不能替代测试、Schema、环境状态和独立证据。
- **一次性替换现有 Task／Loop 编排**：否决，破坏兼容与运行中恢复语义；采用投影、契约、执行器、风险门的分阶段迁移。
