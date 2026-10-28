# ADR-0019：增加 ACK 测试环境部署路径

- **状态**：已接受
- **日期**：2026-10-19

## 背景

团队已有阿里云 ACK 测试集群及 `E:\timeline` 的发布流程，需要在该集群验证 Agent Fleet。ADR-0006 仍将 ECS + Compose 定为 MVP 生产形态；此次部署不代表生产架构迁移。

## 决定

1. 增加部署到现有 ACK 的测试路径，默认 namespace 为 `angrymiao-test`，发布入口为 `deploy/deploy.sh test`。
2. 保留 ADR-0006 的生产决定，不引入 KEDA、横向扩缩或新的托管服务。
3. 网关与 control-plane 共置一个 Pod，以共享当前文件式 MCP 配置；两者暂不独立扩缩。
4. 公网 Ingress 默认不创建；只有显式设置域名与来源 CIDR 白名单时才创建。

## 后果

- 正面：可复用现有 ACK、ACR、TLS Secret 和云盘 StorageClass，先验证镜像与运行流程。
- 约束：ACK 路径是单副本测试部署；Cloud Disk 数据需单独备份，Ingress 开启前需确认 DAO 登录或来源白名单。

## 已考虑的备选

- 直接将 ACK 设为生产默认：暂不采用，仍按 ADR-0006 的成本和运维决策。
- 将网关与 control-plane 拆成独立 Pod：暂不采用，当前配置文件需二者共享，拆分需先把该状态移入受控共享存储/服务。
