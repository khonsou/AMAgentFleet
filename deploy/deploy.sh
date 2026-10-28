#!/usr/bin/env bash
set -Eeuo pipefail

# Build, push and deploy the complete Agent Fleet stack to the ACK cluster.
# Usage: bash deploy/deploy.sh [test|prod]
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
DEPLOY_DIR="$ROOT_DIR/deploy"
K8S_DIR="$DEPLOY_DIR/k8s"
env_value() {
  [[ -f "$DEPLOY_DIR/.env" ]] || return 0
  sed -n "s/^$1=//p" "$DEPLOY_DIR/.env" | head -n 1 | tr -d '\r'
}
DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-$(env_value DEEPSEEK_API_KEY)}"
DATABASE_URL="${DATABASE_URL:-$(env_value DATABASE_URL)}"
FLEET_AUTH_JWT_ISSUER="${FLEET_AUTH_JWT_ISSUER:-$(env_value FLEET_AUTH_JWT_ISSUER)}"
FLEET_AUTH_JWT_PUBLIC_KEY_FILE="${FLEET_AUTH_JWT_PUBLIC_KEY_FILE:-$(env_value FLEET_AUTH_JWT_PUBLIC_KEY_FILE)}"
UV_EXTRAS="${UV_EXTRAS:-$(env_value UV_EXTRAS)}"
IMAGE_TAG="${IMAGE_TAG:-$(env_value IMAGE_TAG)}"
ENVIRONMENT="${1:-test}"

case "$ENVIRONMENT" in
  test) K8S_NAMESPACE="${K8S_NAMESPACE:-angrymiao-test}" ;;
  prod) K8S_NAMESPACE="${K8S_NAMESPACE:-angrymiao-prod}" ;;
  *) echo "用法：bash deploy/deploy.sh [test|prod]" >&2; exit 2 ;;
esac

K8S_CONTEXT="${K8S_CONTEXT:-kubernetes-admin-c72d7454d43804ccc87327b729e670ba4}"
REGISTRY="${REGISTRY:-registry.cn-shenzhen.aliyuncs.com/angrymiao}"
IMAGE_REPO="${IMAGE_REPO:-am-agent-fleet}"
DEERFLOW_TAG="${DEERFLOW_TAG:-am-fleet-base-v2}"
SECRET_NAME="am-agent-fleet-secrets"
AUTH_SECRET_NAME="am-agent-fleet-auth"
IMAGE_PULL_SECRET="aliyun-reg-secret"
TLS_SECRET_NAME="am-tls"
DOCKER_BIN=""
KUBECTL_BIN=""
NODE_BIN=""
FLEET_AUTH_JWT_PUBLIC_KEY_FILE="${FLEET_AUTH_JWT_PUBLIC_KEY_FILE:-$DEPLOY_DIR/dao-jwt-public-key.pem}"
UV_EXTRAS="${UV_EXTRAS:-postgres}"
if [[ "$FLEET_AUTH_JWT_PUBLIC_KEY_FILE" != /* ]]; then
  FLEET_AUTH_JWT_PUBLIC_KEY_FILE="$DEPLOY_DIR/$FLEET_AUTH_JWT_PUBLIC_KEY_FILE"
fi

die() { echo "[am-agent-fleet] 错误：$*" >&2; exit 1; }

resolve_command() {
  local name="$1"
  if command -v "${name}.exe" >/dev/null 2>&1; then
    printf '%s\n' "${name}.exe"
  elif command -v "$name" >/dev/null 2>&1; then
    printf '%s\n' "$name"
  else
    die "找不到命令：$name（也未找到 ${name}.exe）"
  fi
}

kube() { "$KUBECTL_BIN" --context "$K8S_CONTEXT" "$@"; }

docker_path() {
  local path_value="$1"
  if [[ "$DOCKER_BIN" == *.exe ]] && command -v wslpath >/dev/null 2>&1; then
    wslpath -w "$path_value"
  else
    printf '%s\n' "$path_value"
  fi
}

[[ -n "$FLEET_AUTH_JWT_ISSUER" ]] || die "请在 deploy/.env 设置 FLEET_AUTH_JWT_ISSUER"
[[ -f "$FLEET_AUTH_JWT_PUBLIC_KEY_FILE" ]] || die "DAO JWT 公钥文件不存在：$FLEET_AUTH_JWT_PUBLIC_KEY_FILE"
tr -d '\r' < "$FLEET_AUTH_JWT_PUBLIC_KEY_FILE" | grep -Eq '^-----BEGIN (RSA )?PUBLIC KEY-----$' \
  || die "DAO JWT 公钥文件不是 PEM 公钥"
tr -d '\r' < "$FLEET_AUTH_JWT_PUBLIC_KEY_FILE" | grep -Eq '^-----END (RSA )?PUBLIC KEY-----$' \
  || die "DAO JWT 公钥文件 PEM 结束标记无效"

if [[ -n "${IMAGE_TAG:-}" ]]; then
  RELEASE_TAG="$IMAGE_TAG"
else
  RELEASE_TAG="$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)"
  WORKTREE_STATE="$(git -C "$ROOT_DIR" status --porcelain --untracked-files=all 2>/dev/null || true)"
  if [[ -n "$WORKTREE_STATE" ]]; then
    RELEASE_TAG="${RELEASE_TAG}-dirty-$(date +%Y%m%d%H%M%S)"
  fi
fi
[[ "$RELEASE_TAG" =~ ^[A-Za-z0-9_.-]+$ ]] || die "IMAGE_TAG 格式无效"

GATEWAY_IMAGE="$REGISTRY/$IMAGE_REPO:gateway-$ENVIRONMENT-$RELEASE_TAG"
BACKEND_IMAGE="$REGISTRY/$IMAGE_REPO:backend-$ENVIRONMENT-$RELEASE_TAG"
FRONTEND_IMAGE="$REGISTRY/$IMAGE_REPO:frontend-$ENVIRONMENT-$RELEASE_TAG"
LOCAL_GATEWAY_BASE="am-agent-fleet-gateway-base:$DEERFLOW_TAG"

render_manifest() {
  local file="$1"
  cat "$file" | "$NODE_BIN" -e '
      let text = "";
      process.stdin.setEncoding("utf8");
      process.stdin.on("data", chunk => text += chunk).on("end", () => {
        for (const [key, value] of Object.entries({
          __GATEWAY_IMAGE__: process.argv[1],
          __BACKEND_IMAGE__: process.argv[2],
          __FRONTEND_IMAGE__: process.argv[3],
        })) text = text.split(key).join(value);
        process.stdout.write(text);
      });
    ' "$GATEWAY_IMAGE" "$BACKEND_IMAGE" "$FRONTEND_IMAGE"
}

apply_manifest() {
  render_manifest "$1" | kube apply -n "$K8S_NAMESPACE" -f -
}

dry_run_manifest() {
  render_manifest "$1" | kube apply --dry-run=server -n "$K8S_NAMESPACE" -f - >/dev/null
}

apply_storageclass() {
  cat "$K8S_DIR/storageclass.yaml" | kube apply -f -
}

dry_run_storageclass() {
  cat "$K8S_DIR/storageclass.yaml" | kube apply --dry-run=server -f - >/dev/null
}

copy_secret() {
  local name="$1"
  if kube get secret "$name" -n "$K8S_NAMESPACE" >/dev/null 2>&1; then
    return
  fi
  kube get secret "$name" -n angrymiao-test -o json \
    | "$NODE_BIN" -e 'const ns=process.argv[1];let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{const x=JSON.parse(s);process.stdout.write(JSON.stringify({apiVersion:"v1",kind:"Secret",metadata:{name:x.metadata.name,namespace:ns},type:x.type,data:x.data}))})' "$K8S_NAMESPACE" \
    | kube apply -f - >/dev/null \
    || die "Secret 不存在或无法复制：angrymiao-test/$name"
  echo "[am-agent-fleet] 已从 angrymiao-test 复制 Secret：$name"
}

ensure_image_secrets() {
  if [[ "$ENVIRONMENT" == prod ]]; then
    copy_secret "$IMAGE_PULL_SECRET"
    copy_secret "$TLS_SECRET_NAME"
  fi
  kube get secret "$IMAGE_PULL_SECRET" -n "$K8S_NAMESPACE" >/dev/null 2>&1 \
    || die "镜像拉取 Secret 不存在：$K8S_NAMESPACE/$IMAGE_PULL_SECRET"
  kube get secret "$TLS_SECRET_NAME" -n "$K8S_NAMESPACE" >/dev/null 2>&1 \
    || die "TLS Secret 不存在：$K8S_NAMESPACE/$TLS_SECRET_NAME"
}

ensure_app_secret() {
  if kube get secret "$SECRET_NAME" -n "$K8S_NAMESPACE" >/dev/null 2>&1; then
    kube get secret "$SECRET_NAME" -n "$K8S_NAMESPACE" -o jsonpath='{.data.DEEPSEEK_API_KEY}' | grep -q . \
      || die "已有 Secret 缺少 DEEPSEEK_API_KEY：$K8S_NAMESPACE/$SECRET_NAME"
    kube get secret "$SECRET_NAME" -n "$K8S_NAMESPACE" -o jsonpath='{.data.DATABASE_URL}' | grep -q . \
      || die "已有 Secret 缺少 DATABASE_URL：$K8S_NAMESPACE/$SECRET_NAME"
    return
  fi

  [[ -n "${DEEPSEEK_API_KEY:-}" ]] || die "首次部署请先在本机 shell 设置 DEEPSEEK_API_KEY（不要写入仓库或发到聊天）"
  [[ "$DATABASE_URL" =~ ^postgres(ql)?://[^[:space:]]+$ ]] \
    || die "首次部署请在本机 deploy/.env 设置有效的 DATABASE_URL（postgresql://...）"
  [[ ! "$DATABASE_URL" =~ [\?\&]sslmode= ]] \
    || die "DeerFlow 使用 asyncpg；请从 DATABASE_URL 移除 libpq 专用的 sslmode 参数"

  printf '%s\n%s' "$DEEPSEEK_API_KEY" "$DATABASE_URL" | "$NODE_BIN" -e '
      let input = "";
      process.stdin.setEncoding("utf8");
      process.stdin.on("data", chunk => input += chunk).on("end", () => {
        const [deepSeekApiKey, databaseUrl] = input.split(/\r?\n/);
        const [namespace, name] = process.argv.slice(1);
        const b64 = value => Buffer.from(value, "utf8").toString("base64");
        process.stdout.write(`apiVersion: v1\nkind: Secret\nmetadata:\n  name: ${name}\n  namespace: ${namespace}\ntype: Opaque\ndata:\n  DEEPSEEK_API_KEY: ${b64(deepSeekApiKey)}\n  DATABASE_URL: ${b64(databaseUrl)}\n`);
      });
    ' "$K8S_NAMESPACE" "$SECRET_NAME" | kube apply -f - >/dev/null
  echo "[am-agent-fleet] 已在集群创建应用 Secret：$K8S_NAMESPACE/$SECRET_NAME"
}

ensure_auth_secret() {
  cat "$FLEET_AUTH_JWT_PUBLIC_KEY_FILE" | "$NODE_BIN" -e '
      let pem = "";
      process.stdin.setEncoding("utf8");
      process.stdin.on("data", chunk => pem += chunk).on("end", () => {
        const [issuer, namespace, name] = process.argv.slice(1);
        const b64 = value => Buffer.from(value, "utf8").toString("base64");
        process.stdout.write(JSON.stringify({
          apiVersion: "v1",
          kind: "Secret",
          metadata: { name, namespace },
          type: "Opaque",
          data: {
            "dao-jwt-public-key.pem": b64(pem),
            FLEET_AUTH_JWT_ISSUER: b64(issuer),
          },
        }));
      });
    ' "$FLEET_AUTH_JWT_ISSUER" "$K8S_NAMESPACE" "$AUTH_SECRET_NAME" \
    | kube apply -f - >/dev/null
  echo "[am-agent-fleet] 已更新 DAO JWT 验签配置：$K8S_NAMESPACE/$AUTH_SECRET_NAME"
}

check_prerequisites() {
  DOCKER_BIN="$(resolve_command docker)"
  KUBECTL_BIN="$(resolve_command kubectl)"
  NODE_BIN="$(resolve_command node)"
  "$DOCKER_BIN" info >/dev/null 2>&1 || die "Docker daemon 未启动或当前用户无权访问"
  kube config get-contexts "$K8S_CONTEXT" >/dev/null 2>&1 \
    || die "Kubernetes context 不存在：$K8S_CONTEXT（可用 K8S_CONTEXT 覆盖）"
  kube config current-context
  kube get nodes -l node.kubernetes.io/instance-type=ecs.n1.xlarge -o name | grep -q . \
    || die "ACK 中没有 ecs.n1.xlarge 节点，不能应用 cloud_ssd 兼容标签"
  kube label nodes -l node.kubernetes.io/instance-type=ecs.n1.xlarge \
    node.csi.alibabacloud.com/disktype.cloud_ssd=available --overwrite >/dev/null
  kube create namespace "$K8S_NAMESPACE" --dry-run=client -o yaml | kube apply -f - >/dev/null
  if kube get pvc am-agent-fleet-state -n "$K8S_NAMESPACE" >/dev/null 2>&1; then
    STATE_STORAGE_CLASS="$(kube get pvc am-agent-fleet-state -n "$K8S_NAMESPACE" -o jsonpath='{.spec.storageClassName}')"
    [[ "$STATE_STORAGE_CLASS" == am-agent-fleet-ssd-retain ]] \
      || die "PVC am-agent-fleet-state 使用旧 StorageClass：$STATE_STORAGE_CLASS；需先迁移空卷或备份数据"
  fi
  ensure_image_secrets
  ensure_app_secret
  ensure_auth_secret
  echo "[am-agent-fleet] namespace: $K8S_NAMESPACE"
  echo "[am-agent-fleet] DeerFlow: khonsou/deer-flow@$DEERFLOW_TAG"
  echo "[am-agent-fleet] gateway image: $GATEWAY_IMAGE"
  echo "[am-agent-fleet] backend image: $BACKEND_IMAGE"
  echo "[am-agent-fleet] frontend image: $FRONTEND_IMAGE"
  echo "[am-agent-fleet] OAuth callback: https://fleet.angrymiao.com/oauth/callback"
}

validate_manifests() {
  dry_run_storageclass
  dry_run_manifest "$K8S_DIR/core-pvc.yaml"
  dry_run_manifest "$K8S_DIR/core.yaml"
  dry_run_manifest "$K8S_DIR/console-backend.yaml"
  dry_run_manifest "$K8S_DIR/console-web.yaml"
  dry_run_manifest "$K8S_DIR/ingress.yaml"
}

build_and_push() {
  echo "[am-agent-fleet] 构建网关底座（首次可能耗时较长）"
  "$DOCKER_BIN" build \
    --build-arg "UV_EXTRAS=$UV_EXTRAS" \
    -f backend/Dockerfile \
    -t "$LOCAL_GATEWAY_BASE" \
    "https://github.com/khonsou/deer-flow.git#refs/tags/$DEERFLOW_TAG"
  "$DOCKER_BIN" build \
    --build-arg "BASE_IMAGE=$LOCAL_GATEWAY_BASE" \
    -f "$(docker_path "$DEPLOY_DIR/Dockerfile.gateway")" \
    -t "$GATEWAY_IMAGE" \
    "$(docker_path "$ROOT_DIR")"

  echo "[am-agent-fleet] 构建控制面/Console 镜像"
  "$DOCKER_BIN" build -f "$(docker_path "$DEPLOY_DIR/Dockerfile.fleet-python")" \
    -t "$BACKEND_IMAGE" "$(docker_path "$ROOT_DIR")"
  "$DOCKER_BIN" build -f "$(docker_path "$DEPLOY_DIR/Dockerfile.console-frontend")" \
    --build-arg VITE_AUTH_ENABLED=1 \
    --build-arg VITE_AUTH_ORIGIN=https://auth.angrymiao.com \
    --build-arg VITE_OAUTH_CLIENT_ID=am-agent-fleet \
    -t "$FRONTEND_IMAGE" "$(docker_path "$ROOT_DIR")"

  "$DOCKER_BIN" push "$GATEWAY_IMAGE"
  "$DOCKER_BIN" push "$BACKEND_IMAGE"
  "$DOCKER_BIN" push "$FRONTEND_IMAGE"
}

deploy_stack() {
  apply_storageclass
  apply_manifest "$K8S_DIR/core-pvc.yaml"
  kube wait --for=jsonpath='{.status.phase}'=Bound pvc/am-agent-fleet-state -n "$K8S_NAMESPACE" --timeout=5m

  apply_manifest "$K8S_DIR/core.yaml"
  apply_manifest "$K8S_DIR/console-backend.yaml"
  apply_manifest "$K8S_DIR/console-web.yaml"
  kube rollout status deployment/am-agent-fleet-core -n "$K8S_NAMESPACE" --timeout=600s
  kube rollout status deployment/am-agent-fleet-console-backend -n "$K8S_NAMESPACE" --timeout=300s
  kube rollout status deployment/am-agent-fleet-console-web -n "$K8S_NAMESPACE" --timeout=300s
  apply_manifest "$K8S_DIR/ingress.yaml"

  kube get pods,svc,pvc -n "$K8S_NAMESPACE" -l app.kubernetes.io/part-of=am-agent-fleet -o wide
}

check_prerequisites
validate_manifests
build_and_push
deploy_stack

echo "[am-agent-fleet] 部署完成"
echo "[am-agent-fleet] 访问地址：https://fleet.angrymiao.com"
