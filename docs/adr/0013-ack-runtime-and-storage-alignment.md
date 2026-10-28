# ADR-0013：对齐 ACK 运行底座与节点存储类型

- **状态**：已接受
- **日期**：2026-10-19

## 背景

ACK 部署清单引用了镜像中不存在的 `config.fleet.yaml`，且 v1 配置没有开启项目要求的 skill evolution。原 PVC 使用 `cloud_ssd`，当前 ACK 节点支持 `cloud_essd`，导致 Gateway Pod 无法调度。

## 决定

1. ACK 与 Compose 使用 fork tag `am-fleet-base-v2`，将同 tag 的 `config.fleet.yaml` 随网关镜像发布；配置开启 PostgreSQL、数据库 run events、agents API、scheduler 和 skill evolution。
2. ACK 状态卷使用专属 `am-agent-fleet-essd-retain` StorageClass：`cloud_essd`、`WaitForFirstConsumer`、`Retain`。
3. 保持 gateway 与 control-plane 共置、单副本；本次只修复运行基线与当前集群不兼容的存储类型。

## 后果

- Gateway 启动配置与 DeerFlow fork 版本一致；节点可在选定工作节点后再创建同可用区 ESSD。
- StorageClass 是集群级资源；PVC 删除后云盘保留，备份和清理仍由运维负责。
- 本 ADR 取代 ADR-0012 中使用 `glassesweb-ssd-retain` 的 ACK 存储选择。

## 已考虑的备选

- 保留 v1 并关闭 skill evolution：与项目当前功能约定不符。
- 使用现有 `alicloud-disk-essd`：其回收策略为 `Delete`，PVC 删除会删除云盘。
- 使用 `cloud_ssd`：当前节点不支持，Gateway 无法调度。
