# AMAgentFleet CI/CD

## 版本号

发布前先看当前最新 tag，按版本递增：

```bash
git fetch --tags
git tag --sort=-v:refname | head -5
```

例如最新是 `v0.0.7`，就发 `v0.0.8`；不要复用已有版本。

## 发布

1. 代码合入 `main`。
2. 在 `main` 上打 tag 并推送：

```bash
cd /e/AMAgentFleet
git checkout main
git pull --ff-only
git tag v0.0.8
git push origin v0.0.8
```

3. 等待 GitHub Actions `ci` 构建完成。它会推送 gateway/backend/frontend 镜像到 ACR，并更新 `deploy-fleet-live` 分支。
4. Argo CD 同步：

```bash
argocd app diff am-agent-fleet-test
argocd app sync am-agent-fleet-test
```

5. 验证：

```bash
curl -fsS https://fleet.angrymiao.com/api/console/health
```

## 手动触发

GitHub → Actions → `ci` → Run workflow：

- `image_tag`：留空构建当前 commit；填已有 sha suffix 则跳过构建
- `runner`：`github-hosted`
- `push_image`：推 ACR
- `release`：更新 `deploy-fleet-live`

## 回滚

用旧镜像 suffix 重跑 workflow（`image_tag=sha-xxxx`，`release=true`），然后在 Argo CD Sync。

## 约定

- 触发 tag：`v*`
- 部署分支：`deploy-fleet-live`，CI 只推这个分支，不推 main
- Application：`am-agent-fleet-test`
- 镜像 tag：`gateway-test-sha-<commit>`、`backend-test-sha-<commit>`、`frontend-test-sha-<commit>`
- tag 必须打在 `main` 上，不要在 `deploy-fleet-live` 上打 tag 或提交代码