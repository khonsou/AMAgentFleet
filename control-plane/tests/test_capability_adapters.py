from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.capability_adapters import McpAdapter
from app.capability_plane import CapabilityError, CapabilityService, MemoryStore

ROOT = Path(__file__).resolve().parents[2]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, value, status=200):
        payload = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/health":
            return self.reply({"status": "ok"})
        if self.path == "/api/agents/remote-agent":
            return self.reply({"registry": {"status": "running", "config_version": 3}, "yaml": {}})
        if self.path == "/api/tasks/1":
            return self.reply({
                "task": {"id": 1},
                "loops": [{
                    "id": 2,
                    "execution_status": "succeeded",
                    "is_terminal": True,
                    "deliverable": json.dumps({"value": 7}),
                }],
            })
        return self.reply({"detail": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/api/echo":
            return self.reply({**body, "remote": True})
        if self.path == "/api/agents/remote-agent/dispatch":
            return self.reply({"task_id": 1}, 202)
        if self.path in ("/mcp", "/mcp/"):
            method = body.get("method")
            request_id = body.get("id")
            if method == "initialize":
                return self.reply({"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2024-11-05"}})
            if method == "notifications/initialized":
                return self.reply({"jsonrpc": "2.0", "result": {}})
            if method == "tools/list":
                return self.reply({"jsonrpc": "2.0", "id": request_id, "result": {"tools": [{"name": "observe"}]}})
            if method == "tools/call":
                value = (body.get("params", {}).get("arguments") or {}).get("value")
                return self.reply({"jsonrpc": "2.0", "id": request_id, "result": {"structuredContent": {"value": value, "source": "mcp-http"}}})
        return self.reply({"detail": "not found"}, 404)


class CapabilityAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.service = CapabilityService(MemoryStore(), allow_mock=True)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def manifest(self, provider_id, provider_type, config, operation="observe"):
        return {
            "provider_id": provider_id,
            "provider_type": provider_type,
            "capabilities": [{
                "operation_id": operation,
                "input_schema": {"type": "object"},
                "output_schema": {},
                "output_artifact": "Observation",
                **({"request": {"method": "POST", "path": "/api/echo"}} if operation == "custom_operation" else {}),
            }],
            "config": config,
        }

    async def test_remote_http_operation_registration_verify_and_invoke(self):
        await self.service.register_provider(self.manifest(
            "remote-http", "remote_agent",
            {"protocol": "http", "base_url": self.base_url, "allow_private": True},
            operation="custom_operation",
        ))
        health = await self.service.verify_provider("remote-http")
        self.assertTrue(health["health"]["verified"])
        result = await self.service.invoke("remote-http/custom_operation", {"value": 3})
        self.assertEqual(result["output"], {"value": 3, "remote": True})
        self.assertEqual(result["adapter_evidence"]["path"], "/api/echo")

    async def test_remote_fap_agent_verify_and_invoke(self):
        await self.service.register_provider(self.manifest(
            "remote-agent", "remote_agent",
            {"protocol": "fap", "base_url": self.base_url, "allow_private": True, "agent_name": "remote-agent"},
            operation="analyze",
        ))
        health = await self.service.verify_provider("remote-agent")
        self.assertEqual(health["health"]["remote_config_version"], 3)
        result = await self.service.invoke("remote-agent/analyze", {"goal": "test"})
        self.assertEqual(result["output"], {"value": 7})
        self.assertEqual(result["adapter_evidence"]["task_id"], 1)

    async def test_mcp_stdio_verify_and_invoke(self):
        await self.service.register_provider(self.manifest(
            "mcp-stdio", "mcp",
            {
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(ROOT / "control-plane/tests/fixtures/mock_mcp_server.py")],
                "env": {"MOCK_SETTING": "passed"},
            },
        ))
        health = await self.service.verify_provider("mcp-stdio")
        self.assertTrue(health["health"]["verified"])
        result = await self.service.invoke("mcp-stdio/observe", {"value": 5})
        self.assertEqual(result["output"], {"value": 5, "source": "passed"})

    async def test_mcp_http_verify_and_invoke(self):
        await self.service.register_provider(self.manifest(
            "mcp-http", "mcp",
            {"transport": "http", "base_url": self.base_url + "/mcp", "allow_private": True},
        ))
        health = await self.service.verify_provider("mcp-http")
        self.assertTrue(health["health"]["verified"])
        result = await self.service.invoke("mcp-http/observe", {"value": 6})
        self.assertEqual(result["output"], {"value": 6, "source": "mcp-http"})

    async def test_mcp_result_envelope_is_unwrapped(self):
        provider = {"manifest": self.manifest(
            "mcp-envelope", "mcp",
            {"transport": "http", "base_url": self.base_url + "/mcp", "allow_private": True},
        )}
        with patch(
            "app.capability_adapters.McpSession.request",
            new=AsyncMock(return_value={"structuredContent": {"result": json.dumps({"status": 200})}}),
        ):
            output, _ = await McpAdapter().invoke(provider, provider["manifest"]["capabilities"][0], {})
        self.assertEqual(output, {"status": 200})

    async def test_auth_handle_is_not_forwarded_or_read(self):
        manifest = self.manifest(
            "remote-auth", "remote_agent",
            {"protocol": "http", "base_url": self.base_url, "allow_private": True},
        )
        manifest["auth_handle"] = "vault:remote-credential"
        await self.service.register_provider(manifest)
        with self.assertRaises(CapabilityError) as caught:
            await self.service.verify_provider("remote-auth")
        self.assertEqual(caught.exception.code, "AUTH_REQUIRED")

    async def test_private_remote_requires_explicit_test_policy(self):
        await self.service.register_provider(self.manifest(
            "private-remote", "remote_agent",
            {"protocol": "http", "base_url": self.base_url, "allow_private": False},
        ))
        with self.assertRaises(CapabilityError) as caught:
            await self.service.verify_provider("private-remote")
        self.assertEqual(caught.exception.code, "PRIVATE_REMOTE_FORBIDDEN")


if __name__ == "__main__":
    unittest.main()
