#!/usr/bin/env python3
"""Read-only Runtime cold-switch preflight; never starts, stops or mutates services."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from urllib.request import ProxyHandler, build_opener

import asyncpg

ACTIVE_LOOP_SQL = "SELECT count(*) FROM fleet_task_loops WHERE status IN ('claimed','running','returned')"
OPENER = build_opener(ProxyHandler({}))


class PreflightError(RuntimeError):
    pass


def fetch_json(base_url: str, path: str):
    with OPENER.open(f"{base_url.rstrip('/')}{path}", timeout=10) as response:
        return json.load(response)


def check_control_plane(base_url: str, expected_runtime: str) -> dict:
    health = fetch_json(base_url, "/api/health")
    if health.get("status") != "ok":
        raise PreflightError(f"Control Plane 健康检查失败: {health}")
    current = fetch_json(base_url, "/api/runtime/capabilities")
    if current.get("runtime") != expected_runtime:
        raise PreflightError(f"Runtime 不一致：期望 {expected_runtime}，实际 {current.get('runtime')}")
    protocol = fetch_json(base_url, "/api/ops/protocol")
    operation_ids = {item["id"] for item in protocol.get("operations", [])}
    if "system.runtime_capabilities" not in operation_ids:
        raise PreflightError("协议缺少 system.runtime_capabilities")
    return current


async def check_drained(pg_dsn: str) -> None:
    conn = await asyncpg.connect(pg_dsn)
    try:
        active = await conn.fetchval(ACTIVE_LOOP_SQL)
    finally:
        await conn.close()
    if active:
        raise PreflightError(f"仍有 {active} 个 claimed/running/returned Loop，禁止冷切换")


async def preflight(base_url: str, expected_runtime: str, pg_dsn: str) -> dict:
    current = check_control_plane(base_url, expected_runtime)
    await check_drained(pg_dsn)
    return current


def main() -> None:
    parser = argparse.ArgumentParser(description="Fleet Runtime 冷切换只读预检")
    parser.add_argument("--control-plane", default="http://127.0.0.1:8003")
    parser.add_argument("--expected-runtime", required=True, choices=["deerflow", "deepseek-thin"])
    parser.add_argument("--pg-dsn", default=os.environ.get("FLEET_PG_DSN", ""))
    args = parser.parse_args()
    if not args.pg_dsn:
        parser.error("--pg-dsn 或 FLEET_PG_DSN 必须提供")
    result = asyncio.run(preflight(args.control_plane, args.expected_runtime, args.pg_dsn))
    print(json.dumps({"status": "ok", "drained": True, **result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
