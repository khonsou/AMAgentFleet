#!/usr/bin/env bash
# 本地活动 Run 取消实测：调用一次模型；创建临时 Agent/Task，完成后全部清理。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${FLEET_SMOKE_PORT:-8004}"
LOG="$ROOT/artifacts/thirdparty-cancel-smoke-control-plane.log"
mkdir -p "$ROOT/artifacts"
TMP="$(mktemp -d "$ROOT/artifacts/thirdparty-cancel-smoke.XXXXXX")"
AGENT_NAME="thirdparty-cancel-smoke-$(date +%s)-$$"
TASK_ID=""
THREAD_ID=""
AGENT_CREATED=0
PID=""

cleanup() {
  trap - EXIT INT TERM
  BASE="http://127.0.0.1:$PORT"
  if [[ -n "$TASK_ID" ]]; then
    curl --noproxy '*' -sS -X POST "$BASE/api/tasks/$TASK_ID/close?author=cancel-smoke" \
      >/dev/null 2>&1 || true
  fi
  if [[ -n "$THREAD_ID" ]]; then
    curl --noproxy '*' -sS -X DELETE "$BASE/api/console/threads/$THREAD_ID" \
      >/dev/null 2>&1 || true
  fi
  if [[ "$AGENT_CREATED" = 1 ]]; then
    curl --noproxy '*' -sS -X DELETE \
      "http://127.0.0.1:8003/api/agents/$AGENT_NAME?actor=cancel-smoke" \
      >/dev/null 2>&1 || true
  fi
  if [[ -n "$PID" ]]; then
    kill "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  printf '端口 %s 已占用；请选择空闲的 FLEET_SMOKE_PORT。\n' "$PORT" >&2
  exit 1
fi
curl --noproxy '*' -fsS http://127.0.0.1:8001/health >/dev/null
curl --noproxy '*' -fsS http://127.0.0.1:8003/api/health >/dev/null
curl --noproxy '*' -fsS http://127.0.0.1:8003/api/runtime/capabilities | \
  jq -e '.capabilities.cancel == true' >/dev/null

(
  cd "$ROOT/control-plane"
  export PYTHONPATH="$ROOT:$ROOT/control-plane"
  export FLEET_RUNTIME=deerflow
  exec ../console/backend/.venv/bin/python -m uvicorn app.main:app \
    --host 127.0.0.1 --port "$PORT" >"$LOG" 2>&1
) &
PID=$!
READY=0
for _ in $(seq 1 30); do
  if curl --noproxy '*' -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 1
done
[[ "$READY" = 1 ]] || { printf '控制面启动失败，日志：%s\n' "$LOG" >&2; exit 1; }

jq -n --arg name "$AGENT_NAME" '{yaml:{
  apiVersion:"agentfleet/v1",kind:"Agent",
  metadata:{name:$name,displayName:"Third-party cancel smoke",owner:"qa-team",costCenter:"QA-001"},
  spec:{format:"deerflow.custom-agent",version:1,agent:{
    name:$name,display_name:"Third-party cancel smoke",description:"Temporary local cancel contract test",
    model:"deepseek-default",model_settings:{temperature:0},mcp_plugins:[],skills:null,
    tool_groups:[],allowed_subagents:[],memory_enabled:false,
    soul:"Answer the user directly in plain text. Never use tools or external systems."
  }},egress:[],triggers:{web:true},identity:{mode:"obo",allowedCallers:["qa-team"]},
  budget:{maxTokensPerRun:20000}
}}' >"$TMP/agent.json"
curl --noproxy '*' -fsS -X POST "http://127.0.0.1:8003/api/agents?actor=cancel-smoke" \
  -H 'Content-Type: application/json' --data-binary "@$TMP/agent.json" >"$TMP/agent-response.json"
AGENT_CREATED=1

jq -n --arg assignee "$AGENT_NAME" '{
  input:"Without using any tools, write a very long and detailed technical explanation of database transaction isolation. Do not stop early.",
  assignee:$assignee,source:"agent",created_by:"cancel-smoke",subagent_model_policy:"auto"
}' >"$TMP/task.json"
curl --noproxy '*' -fsS -X POST "http://127.0.0.1:8003/api/tasks" \
  -H 'Content-Type: application/json' --data-binary "@$TMP/task.json" >"$TMP/task-response.json"
TASK_ID="$(jq -r .task_id "$TMP/task-response.json")"
[[ "$TASK_ID" =~ ^[0-9]+$ ]]

ACTIVE=0
for _ in $(seq 1 60); do
  DETAIL="$(curl --noproxy '*' -fsS "http://127.0.0.1:8003/api/tasks/$TASK_ID")"
  THREAD_ID="$(jq -r '.loops[-1].thread_id // empty' <<<"$DETAIL")"
  if [[ -n "$THREAD_ID" ]]; then
    RUNTIME_CODE="$(curl --noproxy '*' -sS -o "$TMP/runtime.json" -w '%{http_code}' \
      "http://127.0.0.1:$PORT/api/console/threads/$THREAD_ID")"
    if [[ "$RUNTIME_CODE" = 200 ]] && \
        jq -e '.runs | any(.status == "running")' "$TMP/runtime.json" >/dev/null; then
      ACTIVE=1
      break
    fi
  fi
  sleep 1
done
[[ "$ACTIVE" = 1 ]] || { printf '未观察到活动 Run；未执行 close 成功断言。\n' >&2; exit 1; }

curl --noproxy '*' -fsS -X POST "http://127.0.0.1:$PORT/api/tasks/$TASK_ID/close?author=cancel-smoke" \
  >"$TMP/close.json"
jq -e '.pool_status == "done" and .cancelled_runs >= 1' "$TMP/close.json" >/dev/null
curl --noproxy '*' -fsS "http://127.0.0.1:$PORT/api/console/threads/$THREAD_ID" \
  >"$TMP/cancelled-runtime.json"
jq -e '.runs | any(.status == "interrupted")' "$TMP/cancelled-runtime.json" >/dev/null

printf '真实活动 Run 取消通过：close 返回 cancelled_runs=%s，Runtime 状态 interrupted。\n' \
  "$(jq -r .cancelled_runs "$TMP/close.json")"
