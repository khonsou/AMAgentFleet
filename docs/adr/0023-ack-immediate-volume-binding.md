# ADR-0023：ACK 状态盘使用立即绑定

- **状态**：已接受
- **日期**：2026-10-19

## 背景

ACK 三台 `ecs.n1.xlarge` 节点都位于 `cn-shenzhen-a`。`cloud_efficiency` PVC 使用 `WaitForFirstConsumer` 时，调度器无法在当前资源余量下完成首轮节点/卷选择；集群已有同类 Immediate StorageClass。

## 决定

1. `am-agent-fleet-efficiency-retain` 使用 `Immediate` 绑定，保留 `cloud_efficiency` 和 `Retain`。
2. 本 ADR 仅取代 ADR-0022 的 `WaitForFirstConsumer` 绑定策略；磁盘类型、容量和保留策略不变。

## 后果

- PVC 可在 Pod 调度前完成动态供盘；当前所有工作节点同区，立即供盘不会选到不兼容可用区。
- 若节点池未来跨可用区，需重新评估拓扑约束或改回延迟绑定。

## 已考虑的备选

- 保留 `WaitForFirstConsumer`：当前 CSI/调度组合无法完成首次节点选择。
- 复用集群默认 `alicloud-disk-efficiency`：其回收策略是 `Delete`，PVC 删除会删除磁盘。
