#!/usr/bin/env bash
# Fleet Operations Protocol 统一验收：默认包含真实服务与模型；--offline 仅跑确定性回归。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
MODE="${1:-all}"
if [[ "$MODE" != "all" && "$MODE" != "--offline" ]]; then
  echo "用法：$0 [--offline]" >&2
  exit 2
fi

GATEWAY_PYTHON="$ROOT/spike/deer-flow/backend/.venv/bin/python"
CONSOLE_PYTHON="$ROOT/console/backend/.venv/bin/python"
for executable in "$GATEWAY_PYTHON" "$CONSOLE_PYTHON" "$ROOT/console/frontend/node_modules/.bin/vitest"; do
  [[ -x "$executable" ]] || { echo "缺少测试依赖：$executable" >&2; exit 1; }
done

echo "[1/7] Python 语法与部署配置检查"
"$CONSOLE_PYTHON" -m py_compile \
  fleet_tools.py scripts/fleet_ops_activate.py scripts/fleet_ops_acceptance.py \
  control-plane/app/*.py deploy/prepare_gateway_config.py

echo "[2/7] Control Plane 单元与协议覆盖测试"
(
  cd control-plane
  PYTHONPATH="$ROOT:$ROOT/control-plane" "$GATEWAY_PYTHON" -m unittest discover -s tests -v
)

echo "[3/7] Console 后端回归测试"
(
  cd console/backend
  "$CONSOLE_PYTHON" -m unittest test_main -v
)

echo "[4/7] 前端组件、导航、类型、构建与 lint 检查"
npm --prefix console/frontend test
npm --prefix console/frontend run build
npm --prefix console/frontend run lint

if [[ "$MODE" == "--offline" ]]; then
  echo "离线验收完成（未调用在线服务或模型）。"
  exit 0
fi

echo "[5/7] 在线激活与真实只读 Agent 调用"
for url in \
  http://127.0.0.1:8001/health \
  http://127.0.0.1:8002/api/console/health \
  http://127.0.0.1:8003/api/health; do
  curl --noproxy '*' -fsS "$url" >/dev/null
 done
"$CONSOLE_PYTHON" scripts/fleet_ops_activate.py

echo "[6/7] 72 项协议与第三方 PAT 在线验收"
"$CONSOLE_PYTHON" scripts/fleet_ops_acceptance.py

echo "[7/7] Chromium 桌面与移动端浏览器测试"
npm --prefix console/frontend run test:browser

echo "Fleet Operations Protocol 全量验收通过。"
