# ADR-0022：ACK 状态盘匹配 n1 节点规格

- **状态**：已接受
- **日期**：2026-10-19

## 背景

ACK 节点规格为 `ecs.n1.xlarge`。部署时创建的 ESSD 能成功建盘，但 CSI 挂载返回 `InvalidInstanceType.NotSupportDiskCategory`，Gateway 因而无法启动。

## 决定

1. ACK 状态 PVC 改用 `cloud_efficiency`，使用 `am-agent-fleet-efficiency-retain` StorageClass，保留 `WaitForFirstConsumer` 和 `Retain`。
2. 保持 20Gi 单副本状态卷，供 DeerFlow 运行文件、agent memory/facts 和共享 MCP 配置使用。
3. 本 ADR 仅取代 ADR-0020 的 ESSD 存储选择；底座镜像与其余运行配置仍按 ADR-0020 执行。

## 后果

- 磁盘类别与当前 n1 节点支持范围匹配；该类 I/O 足以承载小型运行状态文件。
- PVC 删除后磁盘保留，仍需备份与清理；Pod 调度会触发延迟绑定。

## 已考虑的备选

- ESSD：当前节点规格不支持，CSI 返回明确错误。
- 复用 Timeline NAS：否决，跨应用共享会混淆数据生命周期和故障边界。
