# ADR-0025：ACK 状态盘固定到兼容可用区

- **状态**：已接受
- **日期**：2026-10-19

## 背景

ACK 节点为 `ecs.n1.xlarge`，支持高效云盘。无 `zoneId` 的 StorageClass 生成了带节点磁盘类别标签的 PV 拓扑要求，但节点未公布该标签；集群中可正常使用的磁盘类通过显式 `zoneId` 生成可用区拓扑。

## 决定

1. 状态卷使用 `cloud_efficiency`，StorageClass `am-agent-fleet-efficiency-retain` 显式指定 `zoneId: cn-shenzhen-a`、`Immediate` 和 `Retain`。
2. 保持 20Gi 单副本 PVC；本 ADR 取代 ADR-0024 的 `cloud_ssd` 选择，保留 ADR-0023 的立即绑定策略。

## 后果

- PV 拓扑限定在当前三台节点所在可用区，避免 CSI 生成节点磁盘类别标签筛选。
- 节点池若跨区，需同步调整 StorageClass 的 zone；PVC 删除后云盘保留。

## 已考虑的备选

- ESSD：当前 ECS 实例规格不支持挂载。
- SSD：更高性能对运行状态文件没有实际收益；高效云盘已满足容量与 I/O 需求。
