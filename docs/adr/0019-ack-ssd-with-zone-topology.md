# ADR-0019：ACK 状态盘沿用已验证的 SSD 拓扑

- **状态**：已接受
- **日期**：2026-10-19

## 背景

ACK 中现有 `glassesweb-ssd-retain` 使用 `cloud_ssd` 与 `zoneId: cn-shenzhen-a`，其 PV 拓扑只限定可用区且已在 n1 节点运行。Fleet 的新卷未声明 `zoneId` 时会增加当前节点没有的磁盘类别标签约束；高效云盘即使指定可用区仍会生成该约束。

## 决定

1. Fleet 状态盘使用 `cloud_ssd` 并显式指定 `zoneId: cn-shenzhen-a`，使用独立的 `am-agent-fleet-ssd-retain`、立即绑定与 Retain。
2. 保持 20Gi 单副本；本 ADR 取代 ADR-0018 的 `cloud_efficiency` 类别，并沿用 ADR-0016 的立即绑定策略。

## 后果

- PV 使用可用区拓扑，复用当前集群已验证的磁盘类型和 CSI 配置。
- 节点池跨区时需调整 zone；PVC 删除后磁盘保留。

## 已考虑的备选

- ESSD：当前 ECS 实例类型不支持。
- 高效云盘：节点 CSI 标签约束与当前节点池不匹配。
