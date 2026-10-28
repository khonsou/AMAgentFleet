#!/usr/bin/env bash
# Agent Fleet Console 一键开发环境
# 拉起：PostgreSQL(pgserver 内嵌) → DeerFlow 网关(8001) → Console 后端(8002) → Control Plane(8003) → Vite 前端
# 退出时（Ctrl+C / 预览关闭）全部回收。
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SPIKE="$REPO_ROOT/spike"
DEERFLOW="$SPIKE/deer-flow"
PGBIN="$SPIKE/pgenv/lib/python3.12/site-packages/pgserver/pginstall/bin"
PGDATA="$SPIKE/pgdata"
LOGS="$REPO_ROOT/console/logs"
mkdir -p "$LOGS"
# 启动器与手工运行共用同一份日志；没有新记录 = 预览启动器未调用脚本。
exec > >(/usr/bin/tee -a "$LOGS/dev.log") 2>&1
echo "[dev] $(date '+%Y-%m-%d %H:%M:%S') 启动：npm run dev $*"

# 预览进程的 PATH 可能不含 Homebrew / 用户 bin，显式定位 uv。
UV_BIN="${UV_BIN:-}"
if [ -z "$UV_BIN" ]; then
  for candidate in "$(command -v uv 2>/dev/null || true)" "$HOME/.local/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then UV_BIN="$candidate"; break; fi
  done
fi
if [ -z "$UV_BIN" ] || [ ! -x "$UV_BIN" ]; then
  echo "[dev] 找不到 uv；请安装 uv 或通过 UV_BIN 指定绝对路径"
  exit 1
fi

STARTED_PG=0
VITE_PID=""
CLEANED_UP=0
cleanup() {
  [ "$CLEANED_UP" = 1 ] && return
  CLEANED_UP=1
  trap - EXIT INT TERM
  echo ""
  echo "[dev] 正在回收本次启动的进程…"
  local name pid
  [ -n "$VITE_PID" ] && kill "$VITE_PID" 2>/dev/null || true
  for name in gateway console-backend control-plane; do
    if [ -f "$LOGS/$name.pid" ]; then
      pid="$(cat "$LOGS/$name.pid")"
      kill "$pid" 2>/dev/null || true
      rm -f "$LOGS/$name.pid"
    fi
  done
  if [ "$STARTED_PG" = 1 ]; then
    "$PGBIN/pg_ctl" -D "$PGDATA" stop -m fast >/dev/null 2>&1
  fi
  echo "[dev] 已停止本次启动的服务"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# 1. PostgreSQL
if ! "$PGBIN/psql" "postgresql://postgres@127.0.0.1:5432/deerflow" -c "SELECT 1" >/dev/null 2>&1; then
  echo "[dev] 启动 PostgreSQL…"
  "$PGBIN/pg_ctl" -D "$PGDATA" -l "$PGDATA/server.log" \
    -o "-p 5432 -h 127.0.0.1 -k $PGDATA" -w start >/dev/null || { echo "[dev] PG 启动失败"; exit 1; }
  STARTED_PG=1
else
  echo "[dev] PostgreSQL 已在运行（退出时保留）"
fi

# 仅回收本进程新启动的服务；复用已有服务时清除旧 PID 记录。
rm -f "$LOGS/gateway.pid" "$LOGS/control-plane.pid"

# 2. DeerFlow 网关
if ! curl --noproxy '*' -s --max-time 2 http://127.0.0.1:8001/health >/dev/null 2>&1; then
  echo "[dev] 启动 DeerFlow 网关…"
  (
    cd "$DEERFLOW/backend"
    # PYTHONPATH 必须含仓库根：config.yaml 的 fleet 工具组经 use: fleet_tools:* 加载
    PYTHONPATH="$REPO_ROOT:$DEERFLOW/backend" \
    DEER_FLOW_PROJECT_ROOT="$DEERFLOW" \
    DEER_FLOW_HOME="$DEERFLOW/backend/.deer-flow" \
    DEER_FLOW_AUTH_DISABLED=1 \
    NO_PROXY="localhost,127.0.0.1,::1" \
    no_proxy="localhost,127.0.0.1,::1" \
    "$UV_BIN" run --no-sync uvicorn app.gateway.app:app --host 127.0.0.1 --port 8001 \
      > "$LOGS/gateway.log" 2>&1 &
    echo $! > "$LOGS/gateway.pid"
  )
  for i in $(seq 1 30); do
    curl --noproxy '*' -s --max-time 2 http://127.0.0.1:8001/health >/dev/null 2>&1 && break
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
  curl --noproxy '*' -s --max-time 2 http://127.0.0.1:8002/api/console/health >/dev/null 2>&1 && break
  sleep 1
  [ "$i" = "15" ] && { echo "[dev] Console 后端启动超时，见 console/logs/console-backend.log"; exit 1; }
done
echo "[dev] Console 后端就绪 (8002)"

# 4. Control Plane（Registry / Dispatcher / Inbox / Scheduler）
if ! curl --noproxy '*' -s --max-time 2 http://127.0.0.1:8003/api/agents >/dev/null 2>&1; then
  echo "[dev] 启动 Control Plane…"
  (
    cd "$REPO_ROOT/control-plane"
    ../console/backend/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8003 \
      > "$LOGS/control-plane.log" 2>&1 &
    echo $! > "$LOGS/control-plane.pid"
  )
  for i in $(seq 1 15); do
    curl --noproxy '*' -s --max-time 2 http://127.0.0.1:8003/api/agents >/dev/null 2>&1 && break
    sleep 1
    [ "$i" = "15" ] && { echo "[dev] Control Plane 启动超时，见 console/logs/control-plane.log"; exit 1; }
  done
else
  echo "[dev] Control Plane 已在运行"
fi
echo "[dev] Control Plane 就绪 (8003)"

# 5. Vite 前端（参数透传；直接管理 PID，避免 npm 套娃吞掉预览停止信号）
echo "[dev] 启动前端…"
cd "$REPO_ROOT/console/frontend"
./node_modules/.bin/vite "$@" &
VITE_PID=$!
wait "$VITE_PID"
