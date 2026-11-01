#!/usr/bin/env bash
# Read-only local stack health check; never starts or stops processes.
set -uo pipefail

FRONTEND_URL="${1:-http://localhost:7102}"
FAILED=0

check() {
  local name="$1" url="$2"
  if curl --noproxy '*' -fsS --max-time 3 "$url" >/dev/null 2>&1; then
    echo "OK      $name $url"
  else
    echo "MISSING $name $url"
    FAILED=1
  fi
}

check "Gateway:8001" "http://127.0.0.1:8001/health"
check "Console:8002" "http://127.0.0.1:8002/api/console/health"
check "ControlPlane:8003" "http://127.0.0.1:8003/api/health"
check "RuntimeCapabilities" "http://127.0.0.1:8003/api/runtime/capabilities"
check "Frontend" "$FRONTEND_URL"

if [ "$FAILED" -ne 0 ]; then
  cat >&2 <<'EOF'

完整 Fleet 环境未就绪。仅运行 Vite 会导致所有页面同时报 Error: null。
恢复：cd console && npm run dev
排障：先确认 8001/8002/8003 均在监听，再刷新页面；不要只启动 dev:vite。
EOF
  exit 1
fi

echo "Fleet stack is ready."
