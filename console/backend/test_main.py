import os
import unittest
from unittest.mock import patch

import httpx

import main


class ControlPlaneClientTest(unittest.IsolatedAsyncioTestCase):
    @patch.dict(os.environ, {"NO_PROXY": "localhost,127.0.0.1,::1,[::1]"})
    async def test_ignores_malformed_proxy_environment(self):
        """本机服务转发不应被 shell 的代理/no_proxy 配置影响。"""

        async def fake_request(self, method, url, **kwargs):
            assert method == "GET"
            assert url == "/api/agents"
            return httpx.Response(200, json=[])

        with patch.object(httpx.AsyncClient, "request", fake_request):
            self.assertEqual(await main._cp("/agents"), [])

    async def test_blank_thread_keeps_requested_agent_binding(self):
        """空白浮窗会话不能在首次发消息时回退为 lead_agent。"""

        async def fake_post(_client, url, **kwargs):
            self.assertEqual(url, f"{main.GATEWAY}/api/threads")
            self.assertEqual(kwargs["json"], {"assistant_id": "fleet-ops-01"})
            return httpx.Response(200, json={"thread_id": "ops-thread"})

        with patch.object(httpx.AsyncClient, "post", fake_post):
            result = await main.create_thread(
                main.CreateThreadIn(assistant_id="fleet-ops-01")
            )
        self.assertEqual(result, {"thread_id": "ops-thread"})

    async def test_legacy_ops_thread_uses_explicit_identity_on_send(self):
        """旧浮窗会话即使空绑定，也不能静默回退为 lead_agent。"""

        class Conn:
            async def fetchval(self, *_args):
                return None

        class Acquire:
            async def __aenter__(self):
                return Conn()

            async def __aexit__(self, *_args):
                return None

        class Pool:
            def acquire(self):
                return Acquire()

        async def fake_pool():
            return Pool()

        async def fake_post(_client, _url, **kwargs):
            self.assertEqual(kwargs["json"]["assistant_id"], "fleet-ops-01")
            return httpx.Response(200, json={"status": "ok"})

        with patch.object(main, "pool", fake_pool), patch.object(httpx.AsyncClient, "post", fake_post):
            result = await main.send_message(
                "legacy-thread", main.MessageIn(content="查询", assistant_id="fleet-ops-01")
            )
        self.assertEqual(result, {"status": "ok"})

    async def test_send_surfaces_gateway_run_error_as_visible_502(self):
        """Gateway HTTP 200 但 Run=error 时，浮窗必须收到可读失败而不是静默。"""

        class Conn:
            async def fetchval(self, *_args):
                return "fleet-ops-01"

        class Acquire:
            async def __aenter__(self):
                return Conn()

            async def __aexit__(self, *_args):
                return None

        class Pool:
            def acquire(self):
                return Acquire()

        async def fake_pool():
            return Pool()

        async def fake_post(_client, _url, **_kwargs):
            return httpx.Response(200, json={"status": "error", "error": "Recursion limit reached"})

        with patch.object(main, "pool", fake_pool), patch.object(httpx.AsyncClient, "post", fake_post):
            with self.assertRaises(main.HTTPException) as raised:
                await main.send_message(
                    "ops-thread", main.MessageIn(content="新建任务", assistant_id="fleet-ops-01")
                )
        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn("Recursion limit reached", raised.exception.detail)

    async def test_send_rejects_conflicting_thread_identity(self):
        """已绑定其他 Agent 的会话不能被浮窗静默接管。"""

        class Conn:
            async def fetchval(self, *_args):
                return "lead_agent"

        class Acquire:
            async def __aenter__(self):
                return Conn()

            async def __aexit__(self, *_args):
                return None

        class Pool:
            def acquire(self):
                return Acquire()

        async def fake_pool():
            return Pool()

        with patch.object(main, "pool", fake_pool):
            with self.assertRaises(main.HTTPException) as raised:
                await main.send_message(
                    "other-thread", main.MessageIn(content="查询", assistant_id="fleet-ops-01")
                )
        self.assertEqual(raised.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
