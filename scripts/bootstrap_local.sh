#!/usr/bin/env bash
# 全新机器本地环境一键重建：git clone 之后、`cd console && npm run dev` 之前跑这个。
#
# 覆盖 gitignore 排除的所有本机资产：
#   spike/deer-flow     DeerFlow 底座（fork tag am-fleet-base-v2 的 tarball，非 git clone）
#   spike/pgenv         内嵌 PostgreSQL 的 Python 环境（pgserver）
#   spike/pgdata        PG 数据目录（initdb；已有数据则跳过）
#   console/backend/.venv   Console/Control Plane 依赖（deploy/requirements.txt）
#   console/frontend/node_modules
#   spike/deer-flow/config.yaml   从 deploy/config.fleet.yaml 复制（仓库内真理）
#   spike/deer-flow/.env          骨架；DEEPSEEK_API_KEY 必须人工填写（密钥不入库）
#
# 用法：scripts/bootstrap_local.sh
# 网络慢时先导出代理：export https_proxy=http://127.0.0.1:7890（按本机实际端口）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DEERFLOW_TAG="am-fleet-base-v2"
PGBIN="$ROOT/spike/pgenv/lib/python3.12/site-packages/pgserver/pginstall/bin"

UV_BIN="${UV_BIN:-}"
if [ -z "$UV_BIN" ]; then
  for candidate in "$(command -v uv 2>/dev/null || true)" "$HOME/.local/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then UV_BIN="$candidate"; break; fi
  done
fi
[ -x "$UV_BIN" ] || { echo "[bootstrap] 找不到 uv；先安装 uv 或用 UV_BIN 指定" >&2; exit 1; }

step() { echo ""; echo "[bootstrap] === $* ==="; }

# 1. DeerFlow 底座（tarball 浅下载，避免完整 git 历史；已存在则跳过）
step "DeerFlow 底座 ($DEERFLOW_TAG)"
if [ -d spike/deer-flow/backend ]; then
  echo "[bootstrap] spike/deer-flow 已存在，跳过"
else
  mkdir -p spike
  TARBALL="$(mktemp -t deerflow).tar.gz"
  curl -L --retry 3 -o "$TARBALL" \
    "https://github.com/khonsou/deer-flow/archive/refs/tags/${DEERFLOW_TAG}.tar.gz"
  tar -xzf "$TARBALL" -C spike
  mv "spike/deer-flow-${DEERFLOW_TAG}" spike/deer-flow
  rm -f "$TARBALL"
fi

# 2. 网关 Python 依赖（含 postgres extra）
step "网关依赖 (uv sync --extra postgres)"
( cd spike/deer-flow/backend && "$UV_BIN" sync --extra postgres )

# 3. 内嵌 PostgreSQL 环境与数据目录
step "pgenv + pgdata"
if [ ! -x "$PGBIN/pg_ctl" ]; then
  "$UV_BIN" venv spike/pgenv --python 3.12
  "$UV_BIN" pip install --python spike/pgenv/bin/python pgserver
fi
if [ ! -f spike/pgdata/PG_VERSION ]; then
  mkdir -p spike/pgdata
  "$PGBIN/initdb" -D spike/pgdata -U postgres --auth=trust -E UTF8 >/dev/null
  echo "[bootstrap] pgdata 已初始化（trust 仅限本机回环，由 dev.sh 以 -h 127.0.0.1 启动）"
else
  echo "[bootstrap] spike/pgdata 已存在，跳过 initdb（保留现有数据）"
fi

# 4. Console 后端 / Control Plane 共用 venv
step "console/backend/.venv"
if [ ! -x console/backend/.venv/bin/python ]; then
  "$UV_BIN" venv console/backend/.venv
fi
"$UV_BIN" pip install --python console/backend/.venv/bin/python -r deploy/requirements.txt
# 测试依赖：control-plane 测试经仓库根 PYTHONPATH import fleet_tools（需 langchain_core）
"$UV_BIN" pip install --python console/backend/.venv/bin/python \
  pytest pytest-asyncio langchain-core

# 5. 前端依赖
step "console/frontend node_modules"
( cd console/frontend && npm install )

# 6. 网关配置与密钥骨架
step "config.yaml / .env"
if [ ! -f spike/deer-flow/config.yaml ]; then
  cp deploy/config.fleet.yaml spike/deer-flow/config.yaml
  echo "[bootstrap] config.yaml 已从 deploy/config.fleet.yaml 生成"
else
  echo "[bootstrap] config.yaml 已存在，跳过"
fi
[ -f spike/deer-flow/extensions_config.json ] || \
  cp spike/deer-flow/extensions_config.example.json spike/deer-flow/extensions_config.json
if [ ! -f spike/deer-flow/.env ]; then
  printf 'DATABASE_URL=postgresql://postgres@127.0.0.1:5432/deerflow\nDEEPSEEK_API_KEY=\n' \
    > spike/deer-flow/.env
  chmod 600 spike/deer-flow/.env
  echo "[bootstrap] 已生成 .env 骨架"
fi

step "完成"
if grep -q '^DEEPSEEK_API_KEY=$' spike/deer-flow/.env; then
  echo "[bootstrap] ⚠ 请把 DeepSeek API key 填入 spike/deer-flow/.env 的 DEEPSEEK_API_KEY= 后面"
fi
echo "[bootstrap] 启动全栈：cd console && npm run dev"
