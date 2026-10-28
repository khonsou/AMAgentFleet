# ADR-0024：ACK 状态盘使用节点已验证的 SSD 类别

- **状态**：已接受
- **日期**：2026-10-19

## 背景

ACK 的 ESSD 挂载被 ECS 拒绝，`cloud_efficiency` 则被当前 CSI 调度判为节点不支持。集群已有 `cloud_ssd` 卷正在 `ecs.n1.xlarge` 节点上使用。

## 决定

1. 状态 PVC 使用 `cloud_ssd` 和 `am-agent-fleet-ssd-retain` StorageClass，采用 `Immediate` 绑定与 `Retain` 回收策略。
2. 保持 20Gi 单副本卷及现有挂载位置；本 ADR 取代 ADR-0022 的云盘类别，并沿用 ADR-0023 的立即绑定策略。

## 后果

- 磁盘类别在当前 ACK 节点上已被现有工作负载验证可挂载；容量用于运行文件、agent memory/facts 和共享 MCP 配置。
- PVC 删除后磁盘保留，仍需备份与清理；未来更换节点池时需重新确认磁盘类别兼容性。

## 已考虑的备选

- ESSD：当前实例类型挂载时报 `InvalidInstanceType.NotSupportDiskCategory`。
- 高效云盘：当前 CSI 调度将该类型判为不支持。
