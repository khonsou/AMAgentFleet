# ADR-0027：为 n1 节点声明已验证的 SSD 能力

- **状态**：已接受
- **日期**：2026-10-19

## 背景

当前 CSI PV 拓扑要求节点带 `node.csi.alibabacloud.com/disktype.cloud_ssd=available`，但 ACK 节点没有该标签，导致新卷无法调度；现有 `cloud_ssd` 卷已在同规格节点挂载运行，阿里云规格也确认 n1 支持 SSD 云盘。

## 决定

1. 部署脚本仅对 `node.kubernetes.io/instance-type=ecs.n1.xlarge` 的节点设置 CSI SSD 能力标签。
2. SSD 状态盘继续使用独立 Retain StorageClass；不改动其他节点类型。

## 后果

- 当前 n1 节点可通过 CSI 拓扑调度新 SSD 卷；新加入的 n1 节点在下一次部署时补齐标签。
- 节点池更换规格时，必须重新核对 ECS 磁盘规格并调整脚本筛选条件。

## 已考虑的备选

- 直接公开 Gateway：不能解决磁盘调度问题且会扩大攻击面。
- 复用其他应用的 PV：卷数据和生命周期不能共享。
