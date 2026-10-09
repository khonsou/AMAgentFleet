# ADR-0006：MVP 基础设施为 2 台 ECS + Docker Compose，不引入 Kubernetes

- **状态**：已接受
- **日期**：2026-10-08

## 背景

公司约 40 人，无专职平台工程师；并发峰值约 10 个 agent。调研中 ACP 展示了平台级方案的真实成本：OpenShift 4.22+、Keycloak、Gateway API、沙箱控制器、6 个服务组件——对当前规模，平台本身比业务还重。

## 决定

1. MVP 拓扑：**2 台 ECS（4C8G）+ RDS PostgreSQL 基础版 + Redis + OSS + ALB 公网入口**，容器编排用 Docker Compose，固定 10–12 个 runner 容器。
2. Console（fleet 列表 + agent 对话子页 + 审计页）经 ALB 公网发布，OIDC/OAuth 登录；agent runtime 与业务系统留在 VPC 内。
3. 月成本目标 ≤ ¥3,000（含 DeepSeek token）。
4. **迁移触发条件**（满足其一即评估上 ACK + KEDA）：并发持续 >50；需要 microVM 级沙箱隔离；出现多团队多租户需求。runner 无状态（ADR-0001）使该迁移不换架构。

## 后果

- 正面：运维极简，单机故障靠 checkpoint + 另一台接管恢复；全部组件可用一条 `docker compose up` 复现。
- 负面/约束：无自动弹性（固定池够用）；Compose 不是高可用编排，RDS/Redis 用云托管基础高可用版补偿。

## 已考虑的备选

- 直接上 ACK + KEDA：否决，当前规模为空转而付复杂度税；保留为迁移路径。
- 函数计算 AgentRun（Serverless 运行时）：备选保留，见 ADR-0001。
- 单台 ECS 全部混部：否决，控制面与数据平面至少物理分离到两台，避免 runner 失控拖垮 Registry/Vault。
