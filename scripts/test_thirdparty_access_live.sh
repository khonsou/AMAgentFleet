#!/usr/bin/env bash
# 当前代码的本地真实 API 冒烟：PG + 临时 PAT；不调用模型，不访问公网实例。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${FLEET_SMOKE_PORT:-8004}"
LOG="$ROOT/artifacts/thirdparty-access-smoke-control-plane.log"
mkdir -p "$ROOT/artifacts"
TMP="$(mktemp -d "$ROOT/artifacts/thirdparty-access-smoke.XXXXXX")"
SMOKE="thirdparty-access-$(date +%s)-$$"
PAT_ID=""
TASK_ID=""
PID=""

cleanup() {
  trap - EXIT INT TERM
  if [[ -n "$PAT_ID" ]]; then
    curl --noproxy '*' -sS -X DELETE \
      "http://127.0.0.1:$PORT/api/pats/$PAT_ID?author=thirdparty-smoke" \
      >/dev/null 2>&1 || true
  fi
  if [[ -n "$TASK_ID" ]]; then
    curl --noproxy '*' -sS -X POST \
      "http://127.0.0.1:$PORT/api/tasks/$TASK_ID/close?author=thirdparty-smoke" \
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
for executable in jq curl lsof "$ROOT/console/backend/.venv/bin/python"; do
  command -v "$executable" >/dev/null 2>&1 || [[ -x "$executable" ]] || {
    printf '缺少测试依赖：%s\n' "$executable" >&2
    exit 1
  }
done

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
BASE="http://127.0.0.1:$PORT"

curl --noproxy '*' -fsS "$BASE/api/meta" | jq -e '
  .api_version == "0.2.0" and .deployment_version == "0.2.0" and
  (.capabilities | index("batch_tasks") != null) and
  (.capabilities | index("failure_semantics") != null) and
  (.capabilities | index("runtime_cancel") != null) and
  .authentication.write_implies_read and
  ([.public_operations[] | select(.id == "tasks.conversation")] | length == 1) and
  ([.public_operations[] | select(.id == "system.openapi")] | length == 1)
' >/dev/null

PAT_JSON="$(curl --noproxy '*' -fsS -X POST "$BASE/api/pats" \
  -H 'Content-Type: application/json' \
  -d "{\"name\":\"$SMOKE\",\"scopes\":[\"write\"],\"issuer\":\"thirdparty-smoke\"}")"
PAT_ID="$(jq -r .id <<<"$PAT_JSON")"
TOKEN="$(jq -r .token <<<"$PAT_JSON")"
[[ "$PAT_ID" =~ ^fap-[0-9a-f]{16}$ ]]
[[ "$TOKEN" =~ ^dfp_[0-9A-Za-z]+$ ]]
AUTH="Authorization: Bearer $TOKEN"

MODELS_CODE="$(curl --noproxy '*' -sS -o "$TMP/models.json" -w '%{http_code}' \
  "$BASE/api/models" -H "$AUTH")"
[[ "$MODELS_CODE" = 200 ]]
jq -e 'type == "array"' "$TMP/models.json" >/dev/null

CREATE_CODE="$(curl --noproxy '*' -sS -o "$TMP/create.json" -w '%{http_code}' \
  -X POST "$BASE/api/tasks" -H "$AUTH" -H 'Content-Type: application/json' \
  -H "Idempotency-Key: $SMOKE" \
  -d '{"input":"Third-party API smoke only; do not assign or execute a model.","source":"agent","created_by":"thirdparty-smoke"}')"
[[ "$CREATE_CODE" = 201 ]]
TASK_ID="$(jq -r .task_id "$TMP/create.json")"
[[ "$TASK_ID" =~ ^[0-9]+$ ]]

REPLAY_CODE="$(curl --noproxy '*' -sS -D "$TMP/replay.headers" \
  -o "$TMP/replay.json" -w '%{http_code}' -X POST "$BASE/api/tasks" \
  -H "$AUTH" -H 'Content-Type: application/json' -H "Idempotency-Key: $SMOKE" \
  -d '{"input":"Third-party API smoke only; do not assign or execute a model.","source":"agent","created_by":"thirdparty-smoke"}')"
[[ "$REPLAY_CODE" = 201 ]]
[[ "$(jq -r .task_id "$TMP/replay.json")" = "$TASK_ID" ]]
jq -R -s -e 'split("\n") | map(select(ascii_downcase | startswith("x-idempotent-replay: true"))) | length == 1' \
  "$TMP/replay.headers" >/dev/null

CONFLICT_CODE="$(curl --noproxy '*' -sS -o "$TMP/conflict.json" -w '%{http_code}' \
  -X POST "$BASE/api/tasks" -H "$AUTH" -H 'Content-Type: application/json' \
  -H "Idempotency-Key: $SMOKE" -d '{"input":"different body","source":"agent"}')"
[[ "$CONFLICT_CODE" = 409 ]]

curl --noproxy '*' -fsS "$BASE/api/tasks?ids=24,25,$TASK_ID" -H "$AUTH" | \
  jq -e --argjson id "$TASK_ID" '
    type == "array" and (map(select(.id == $id)) | length == 1) and
    (.[] | select(.id == $id) |
      .execution_status == "queued" and .is_terminal == false and
      .retryable == false and .worker_state == "queued")
  ' >/dev/null

curl --noproxy '*' -fsS -X POST "$BASE/api/tasks/$TASK_ID/close?author=thirdparty-smoke" \
  -H "$AUTH" | jq -e '.pool_status == "done" and .cancelled_runs == 0' >/dev/null
curl --noproxy '*' -fsS "$BASE/api/tasks?ids=$TASK_ID" -H "$AUTH" | \
  jq -e 'length == 1 and .[0].pool_status == "done" and .[0].status == "rejected"' >/dev/null
TASK_ID=""

curl --noproxy '*' -fsS -X DELETE "$BASE/api/pats/$PAT_ID?author=thirdparty-smoke" | \
  jq -e '.status == "revoked"' >/dev/null
PAT_ID=""
REVOKED_CODE="$(curl --noproxy '*' -sS -o "$TMP/revoked.json" -w '%{http_code}' \
  "$BASE/api/models" -H "$AUTH")"
[[ "$REVOKED_CODE" = 401 ]]

printf '本地真实 API 冒烟通过：单 write PAT、meta、实际 PG 批量查询、幂等回放/冲突、close 与 PAT 吊销。\n'
