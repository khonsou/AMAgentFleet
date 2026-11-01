# ADR-0032：RuntimeAdapter 边界与整站冷切换

- **状态**：已接受
- **日期**：2026-11-01

## 背景

Fleet 的 FAP 已统一操作契约，但内部执行仍直接耦合 DeerFlow：Registry 只接受 `deerflow.custom-agent`，Dispatcher／恢复逻辑使用原生 Thread、Run、Checkpoint API，Console 还直接读取 DeerFlow 数据表。ADR-0002 保留了替换 Harness 的方向，当前却没有可实施的运行时隔离层。直接支持第二套 Harness 或同时做 GUI 迁移、状态迁移和多运行时混跑，会把回归面扩大到整个平台。

## 决定

1. **现状不变**：DeerFlow 仍是唯一受支持的内部 Runtime；本 ADR 不选择第二套 Harness，也不授权生产切换。第三方 Agent 继续经 FAP 接入，不等同于替换内部 Runtime。
2. **建立窄 RuntimeAdapter**：Fleet 继续拥有 Registry 组织语义、Task／Loop、Scheduler、验收、审计和凭证边界；Adapter 负责 Agent 部署、Thread／Run 执行与查询、恢复引用、模型目录和能力声明。核心层只使用运行时无关 DTO，不接触 LangGraph 请求结构或 DeerFlow 原生路径。
3. **状态引用不透明化**：Thread、Run、Checkpoint 的外部引用均为 Adapter 管理的不透明标识；恢复错误结构化分类。resume、stream、cancel、feedback、artifacts、subagents 等通过 capabilities 显式声明，不支持时返回明确错误，不伪造等价能力。
4. **先包 DeerFlow**：首个实现必须是 `DeerFlowAdapter`，以保持现有行为为验收标准。原生 Gateway API、YAML 包格式和 LangGraph 细节逐步收敛到 Adapter 及其部署资产；Console 的 DeerFlow 表直读也须经过运行时无关观测面隔离。
5. **协议稳定**：既有 FAP operation ID、Task／Loop 状态机、写确认、幂等和审计语义不变。原生 `gateway` surface 仅作兼容，客户端迁移到稳定 Runtime／Control Plane surface 后才可另行决策废弃。
6. **第一版只支持整站冷切换**：一个部署同时只有一个默认内部 Runtime；切换前停止新任务并排干活动 Loop。旧 Thread／Checkpoint 不跨 Runtime 迁移或续跑，历史会话由 DeerFlow 只读保留或归档。不提供 per-Agent 混跑和运行中热迁移。
7. **配置兼容不等于配置可移植**：现有 `agentfleet/v1` 与 DeerFlow `spec` 保持兼容；运行时专属校验由对应 Adapter 负责。第一版只保证 Fleet API 与 Task／Loop 语义稳定，不承诺同一份 Runtime 专属 YAML 零修改跨 Harness。
8. **安全边界不随 Runtime 移动**：各 Harness 通过受控工具桥连接 Fleet 服务；业务 token、系统 PAT 和秘密仍不得进入 Runner、Thread、日志或审计正文。不得为通用适配开放任意 URL 执行能力。

实施顺序、验收门与回滚点见 [协议化与 RuntimeAdapter 分阶段实施拆解](../dev/protocol-runtime-adapter-rollout.md)；该文档不改变本 ADR 的决策边界。

## 后果

- 正面：第二套 Harness 可以通过小步契约测试接入，Fleet 操作协议和业务状态机无需随运行时重写；DeerFlow 可先作为对照实现验证零行为回归。
- 负面／约束：需要同时改造执行与观测路径，工作量不能压缩成简单 URL 替换；不支持的能力必须显式降级。冷切换需要排干窗口，历史 Runtime 需短期保留只读。
- 暂缓：per-Agent 多 Runtime、活动 Loop 热迁移、跨 Harness Checkpoint 转换和统一可移植 AgentSpec 均需后续独立 ADR，不属于本提案。

## 已考虑的备选

- **一次性替换 DeerFlow 并同时迁移 GUI／数据**：否决，无法隔离回归来源，也无法安全回滚。
- **要求所有 Harness 完整模拟 DeerFlow 原生 API**：否决，会把 LangGraph 与 DeerFlow 语义永久固化；兼容代理只能放在 Adapter 内部。
- **第一版直接支持 per-Agent 混跑和 Checkpoint 迁移**：暂缓，需 Loop 固定 Runtime 绑定、状态转换和恢复语义评审，不属于最小改造。
- **用任意 HTTP operation 代替类型化 Adapter**：否决，既无法保证状态语义，也扩大 SSRF、审计和凭证暴露面。
- **仅靠 FAP 外部执行者替代内部 Adapter**：保留为独立接入方式；它不能满足 Fleet 对内部受管 Agent 的部署、恢复与观测要求。
