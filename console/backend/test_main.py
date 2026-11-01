import os
import unittest
from unittest.mock import patch

import httpx

import main


class ControlPlaneClientTest(unittest.IsolatedAsyncioTestCase):
    @patch.dict(os.environ, {"NO_PROXY": "localhost,127.0.0.1,::1,[::1]"})
    async def test_ignores_malformed_proxy_environment(self):
        async def fake_request(_client, method, url, **kwargs):
            assert method == "GET"
            assert url == "/api/agents"
            return httpx.Response(200, json=[])

        with patch.object(httpx.AsyncClient, "request", fake_request):
            self.assertEqual(await main._cp("/agents"), [])

    async def test_create_thread_forwards_requested_agent_to_control_plane(self):
        async def fake_request(_client, method, url, **kwargs):
            self.assertEqual(method, "POST")
            self.assertEqual(url, "/api/console/threads")
            self.assertEqual(kwargs["json"]["assistant_id"], "fleet-ops-01")
            return httpx.Response(200, json={"thread_id": "ops-thread"})

        with patch.object(httpx.AsyncClient, "request", fake_request):
            result = await main.create_thread(main.CreateThreadIn(assistant_id="fleet-ops-01"))
        self.assertEqual(result, {"thread_id": "ops-thread"})

    async def test_send_forwards_explicit_identity_to_control_plane(self):
        async def fake_request(_client, method, url, **kwargs):
            self.assertEqual(method, "POST")
            self.assertEqual(url, "/api/console/threads/legacy-thread/messages")
            self.assertEqual(kwargs["json"]["assistant_id"], "fleet-ops-01")
            return httpx.Response(200, json={"status": "ok"})

        with patch.object(httpx.AsyncClient, "request", fake_request):
            result = await main.send_message(
                "legacy-thread", main.MessageIn(content="查询", assistant_id="fleet-ops-01"))
        self.assertEqual(result, {"status": "ok"})

    async def test_control_plane_error_remains_visible(self):
        async def fake_request(self, _method, _url, **_kwargs):
            return httpx.Response(502, json={"detail": "Recursion limit reached"})

        with patch.object(httpx.AsyncClient, "request", fake_request):
            with self.assertRaises(main.HTTPException) as raised:
                await main.send_message(
                    "ops-thread", main.MessageIn(content="新建任务", assistant_id="fleet-ops-01"))
        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn("Recursion limit reached", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
