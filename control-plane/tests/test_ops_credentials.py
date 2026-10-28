"""系统 PAT、Vault 与服务端执行代理的安全边界（ADR-0018）。"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "control-plane"))

from app import fap_pats, ops_credentials  # noqa: E402


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class FakeConn:
    def __init__(self):
        self.calls = []

    def transaction(self):
        return FakeTransaction()

    async def execute(self, *args):
        self.calls.append(args)


class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return None


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return FakeAcquire(self.conn)


class FakeResponse:
    status_code = 200
    text = '{"ok":true}'

    def json(self):
        return {"ok": True}


class FakeClient:
    def __init__(self):
        self.request_kwargs = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def request(self, *_args, **kwargs):
        self.request_kwargs = kwargs
        return FakeResponse()


class SystemPatTests(unittest.IsolatedAsyncioTestCase):
    def test_local_vault_encrypts_and_protects_key(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            key_path = Path(tmp) / "vault.key"
            vault = ops_credentials.LocalFernetVault(str(key_path))
            ciphertext = vault.encrypt("dfp_super_secret")
            self.assertNotIn("dfp_super_secret", ciphertext)
            self.assertEqual(vault.decrypt(ciphertext), "dfp_super_secret")
            self.assertEqual(os.stat(key_path).st_mode & 0o777, 0o600)

    async def test_system_identity_cannot_be_claimed_by_normal_issue(self):
        body = fap_pats.PatCreateBody(
            name="fake-system", scopes=["read", "write"], issuer="system:fleet-ops-01")
        with patch("app.fap_pats._assert_loopback"):
            with self.assertRaises(HTTPException) as raised:
                await fap_pats.pat_create(None, body)
        self.assertEqual(raised.exception.status_code, 403)

    async def test_system_pat_cannot_be_revoked(self):
        with patch("app.fap_pats._assert_loopback"):
            with self.assertRaises(HTTPException) as raised:
                await fap_pats.pat_revoke(None, ops_credentials.SYSTEM_OPS_PAT_ID)
        self.assertEqual(raised.exception.status_code, 403)

    def test_system_pat_management_is_local_only_even_with_valid_identity(self):
        request = type("Request", (), {})()
        request.client = type("Peer", (), {"host": "127.0.0.1"})()
        request.state = type("State", (), {
            "fap_pat": {"user_id": ops_credentials.SYSTEM_OPS_USER_ID},
        })()
        request.headers = {}
        fap_pats._assert_loopback(request)
        request.headers = {"x-forwarded-for": "203.0.113.8"}
        with self.assertRaises(HTTPException) as raised:
            fap_pats._assert_loopback(request)
        self.assertEqual(raised.exception.status_code, 403)

    async def test_rotation_persists_only_digest_and_ciphertext(self):
        conn = FakeConn()
        safe_status = {"configured": True, "scopes": ["read", "write"]}

        async def fake_pool():
            return FakePool(conn)

        with patch("app.ops_credentials.pool", fake_pool), \
                patch("app.ops_credentials._vault") as vault, \
                patch("app.ops_credentials.audit", new=AsyncMock()), \
                patch("app.ops_credentials.credential_status", new=AsyncMock(return_value=safe_status)):
            vault.return_value.encrypt.return_value = "encrypted-token"
            result = await ops_credentials.rotate_system_credential()
        self.assertEqual(result, safe_status)
        self.assertNotIn("token", result)
        serialized = json.dumps(conn.calls, default=str)
        self.assertNotIn("dfp_", serialized)
        self.assertIn("encrypted-token", serialized)
        self.assertEqual(len(conn.calls), 2)


class ExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_control_plane_call_injects_pat_only_on_server(self):
        client = FakeClient()
        raw_token = "dfp_server_only_secret"
        payload = ops_credentials.OpsExecuteBody(operation_id="tasks.list")
        with patch("app.fap_middleware._is_internal", return_value=True), \
                patch("app.ops_credentials._system_token", new=AsyncMock(return_value=raw_token)), \
                patch("app.ops_credentials.httpx.AsyncClient", return_value=client) as client_type:
            result = await ops_credentials.execute_operation(None, payload)
        self.assertEqual(client.request_kwargs["headers"]["Authorization"], f"Bearer {raw_token}")
        self.assertNotIn(raw_token, json.dumps(result))
        self.assertEqual(client_type.call_args.kwargs["base_url"], ops_credentials.CONTROL_PLANE_SELF)
        self.assertFalse(client_type.call_args.kwargs["trust_env"])

    async def test_console_call_does_not_receive_control_plane_pat(self):
        client = FakeClient()
        payload = ops_credentials.OpsExecuteBody(operation_id="console.health")
        with patch("app.fap_middleware._is_internal", return_value=True), \
                patch("app.ops_credentials._system_token", new=AsyncMock()) as token, \
                patch("app.ops_credentials.httpx.AsyncClient", return_value=client):
            await ops_credentials.execute_operation(None, payload)
        token.assert_not_awaited()
        self.assertEqual(client.request_kwargs["headers"], {})

    async def test_executor_retains_write_and_human_only_guards(self):
        write = ops_credentials.OpsExecuteBody(operation_id="tasks.create", body={"input": "x"})
        with patch("app.fap_middleware._is_internal", return_value=True):
            with self.assertRaises(HTTPException) as raised:
                await ops_credentials.execute_operation(None, write)
            self.assertEqual(raised.exception.status_code, 409)
            rotate = ops_credentials.OpsExecuteBody(
                operation_id="ops.credential_rotate", confirmed=True)
            with self.assertRaises(HTTPException) as raised:
                await ops_credentials.execute_operation(None, rotate)
            self.assertEqual(raised.exception.status_code, 403)


class VaultMismatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_unreadable_ciphertext_returns_actionable_503(self):
        conn = type("Conn", (), {"fetchval": AsyncMock(return_value="stored-ciphertext")})()

        async def fake_pool():
            return FakePool(conn)

        with patch("app.ops_credentials.ensure_system_credential", new=AsyncMock()), \
                patch("app.ops_credentials.pool", fake_pool), \
                patch("app.ops_credentials._vault") as vault:
            vault.return_value.decrypt.side_effect = ops_credentials.InvalidToken()
            with self.assertRaises(HTTPException) as raised:
                await ops_credentials._system_token()
        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("轮换", raised.exception.detail)

    async def test_startup_check_warns_without_blocking(self):
        conn = type("Conn", (), {"fetchval": AsyncMock(return_value="stored-ciphertext")})()

        async def fake_pool():
            return FakePool(conn)

        with patch("app.ops_credentials.pool", fake_pool), \
                patch("app.ops_credentials._vault") as vault, \
                patch("app.ops_credentials.log") as log:
            vault.return_value.decrypt.side_effect = ops_credentials.InvalidToken()
            await ops_credentials.check_system_credential_readable()
        log.warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
