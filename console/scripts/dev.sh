#!/usr/bin/env bash
# Agent Fleet Console 一键开发环境
# 拉起：PostgreSQL(pgserver 内嵌) → DeerFlow 网关(8001) → Console 后端(8002) → Vite 前端
# 退出时（Ctrl+C / 预览关闭）全部回收。
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SPIKE="$REPO_ROOT/spike"
DEERFLOW="$SPIKE/deer-flow"
PGBIN="$SPIKE/pgenv/lib/python3.12/site-packages/pgserver/pginstall/bin"
PGDATA="$SPIKE/pgdata"
LOGS="$REPO_ROOT/console/logs"
mkdir -p "$LOGS"

PIDS=()
cleanup() {
  echo ""
  echo "[dev] 正在回收进程…"
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null; done
  pkill -f "uvicorn app.gateway.app:app" 2>/dev/null
  pkill -f "uvicorn main:app" 2>/dev/null
  "$PGBIN/pg_ctl" -D "$PGDATA" stop -m fast >/dev/null 2>&1
  echo "[dev] 已全部停止"
}
trap cleanup EXIT INT TERM

# 1. PostgreSQL
if ! "$PGBIN/psql" "postgresql://postgres@127.0.0.1:5432/deerflow" -c "SELECT 1" >/dev/null 2>&1; then
  echo "[dev] 启动 PostgreSQL…"
  "$PGBIN/pg_ctl" -D "$PGDATA" -l "$PGDATA/server.log" \
    -o "-p 5432 -h 127.0.0.1 -k $PGDATA" -w start >/dev/null || { echo "[dev] PG 启动失败"; exit 1; }
else
  echo "[dev] PostgreSQL 已在运行"
fi

# 2. DeerFlow 网关
if ! curl -s --max-time 2 http://127.0.0.1:8001/health >/dev/null 2>&1; then
  echo "[dev] 启动 DeerFlow 网关…"
  (
    cd "$DEERFLOW/backend"
    PYTHONPATH=. \
    DEER_FLOW_PROJECT_ROOT="$DEERFLOW" \
    DEER_FLOW_HOME="$DEERFLOW/backend/.deer-flow" \
    DEER_FLOW_AUTH_DISABLED=1 \
    uv run --no-sync uvicorn app.gateway.app:app --host 127.0.0.1 --port 8001 \
      > "$LOGS/gateway.log" 2>&1 &
    echo $! > "$LOGS/gateway.pid"
  )
  for i in $(seq 1 30); do
    curl -s --max-time 2 http://127.0.0.1:8001/health >/dev/null 2>&1 && break
    sleep 1
    [ "$i" = "30" ] && { echo "[dev] 网关启动超时，见 console/logs/gateway.log"; exit 1; }
  done
else
  echo "[dev] DeerFlow 网关已在运行"
fi
echo "[dev] 网关就绪 (8001)"

# 3. Console 后端
echo "[dev] 启动 Console 后端…"
(
  cd "$REPO_ROOT/console/backend"
  .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8002 \
    > "$LOGS/console-backend.log" 2>&1 &
  echo $! > "$LOGS/console-backend.pid"
)
for i in $(seq 1 15); do
  curl -s --max-time 2 http://127.0.0.1:8002/api/console/health >/dev/null 2>&1 && break
  sleep 1
  [ "$i" = "15" ] && { echo "[dev] Console 后端启动超时，见 console/logs/console-backend.log"; exit 1; }
done
echo "[dev] Console 后端就绪 (8002)"

# 4. Vite 前端（前台运行，参数透传，如 --port 7100）
echo "[dev] 启动前端…"
cd "$REPO_ROOT/console/frontend"
npm run dev -- "$@"
