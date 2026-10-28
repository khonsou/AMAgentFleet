#!/usr/bin/env python3
"""真实本地服务验收：系统 Agent 锁、PAT 轮换与统一执行代理（ADR-0018）。

只在测试进程内存中读取 Vault 明文用于验证旧 token 失效；不打印或保存 token。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import asyncpg
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control-plane"))
from app.ops_credentials import (  # noqa: E402
    SYSTEM_OPS_CREDENTIAL_NAME,
    SYSTEM_OPS_PAT_ID,
    LocalFernetVault,
)

CP = "http://127.0.0.1:8003"
CONSOLE = "http://127.0.0.1:8002"
PG_DSN = "postgresql://postgres@127.0.0.1:5432/deerflow"


async def system_token(conn: asyncpg.Connection) -> str:
    ciphertext = await conn.fetchval(
        "SELECT ciphertext FROM fleet_system_credentials WHERE name=$1",
        SYSTEM_OPS_CREDENTIAL_NAME,
    )
    assert ciphertext, "系统凭证未配置"
    return LocalFernetVault().decrypt(ciphertext)


async def main() -> None:
    conn = await asyncpg.connect(PG_DSN)
    try:
        async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
            status = (await client.get(f"{CONSOLE}/api/console/ops/credential")).json()
            assert status["configured"] and status["scopes"] == ["read", "write"]
            assert not {"token", "ciphertext", "token_digest"} & status.keys()
            old_token = await system_token(conn)
            old_auth = {"Authorization": f"Bearer {old_token}"}
            assert (await client.get(f"{CP}/api/health", headers=old_auth)).status_code == 200
            print("PASS 系统凭证状态与本机 PAT 鉴权")

            detail = (await client.get(f"{CP}/api/agents/fleet-ops-01")).json()
            checks = [
                ("POST", "/api/agents", {"yaml": detail["yaml"]}),
                ("PUT", "/api/agents/fleet-ops-01", {"yaml": detail["yaml"]}),
                ("DELETE", "/api/agents/fleet-ops-01", None),
                ("POST", "/api/agents/fleet-ops-01/stop", {}),
                ("POST", "/api/agents/fleet-ops-01/start", {}),
            ]
            for method, path, body in checks:
                response = await client.request(method, CP + path, json=body)
                assert response.status_code == 403, (method, path, response.text)
            denied = await client.delete(f"{CP}/api/pats/{SYSTEM_OPS_PAT_ID}")
            assert denied.status_code == 403, denied.text
            fake = await client.post(f"{CP}/api/pats", json={
                "name": "fake-system", "scopes": ["read", "write"],
                "issuer": "system:fleet-ops-01",
            })
            assert fake.status_code == 403, fake.text
            print("PASS Registry 与系统 PAT 普通管理入口均锁定")

            rotated = await client.post(f"{CONSOLE}/api/console/ops/credential/rotate")
            assert rotated.status_code == 200, rotated.text
            payload = rotated.json()
            assert payload["version"] == status["version"] + 1
            assert not {"token", "ciphertext", "token_digest"} & payload.keys()
            assert (await client.get(f"{CP}/api/health", headers=old_auth)).status_code == 401
            new_token = await system_token(conn)
            assert new_token != old_token
            new_auth = {"Authorization": f"Bearer {new_token}"}
            assert (await client.get(f"{CP}/api/health", headers=new_auth)).status_code == 200
            print("PASS 人工轮换不回显 token，旧 token 立即失效")

            executed = await client.post(f"{CP}/internal/ops/execute", json={
                "operation_id": "tasks.list", "query": {"limit": 1},
            })
            assert executed.status_code == 200, executed.text
            result = executed.json()
            assert result["status"] == 200 and isinstance(result["result"], list)
            assert old_token not in executed.text and new_token not in executed.text
            unconfirmed = await client.post(f"{CP}/internal/ops/execute", json={
                "operation_id": "tasks.create", "body": {"input": "不应创建"},
            })
            assert unconfirmed.status_code == 409
            print("PASS 执行代理真实 PAT 调用与写确认护栏")

            audit = await client.get(f"{CP}/api/audit", params={"limit": 100})
            assert old_token not in audit.text and new_token not in audit.text
            print("PASS 凭证未进入工具响应或审计正文")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
