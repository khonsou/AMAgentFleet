"""Dispatcher 对外产物链接不得写死本地开发地址。"""

import unittest
from unittest.mock import AsyncMock, patch

from app import dispatcher
from app.dispatcher import _extract_artifacts


class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return None


class RunConnection:
    def __init__(self, profile):
        self.loop = {
            "id": 7, "task_id": 9, "claimant_type": "agent", "claimant": "worker-01",
            "status": "claimed", "thread_id": "4c6a2c52-16f2-4b0d-9882-4f826ea58f6d",
            "model_profile": profile, "resume_comment": None,
        }
        self.execute = AsyncMock()

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def fetchrow(self, query, *_args):
        if "FROM fleet_task_loops WHERE id=$1 FOR UPDATE" in query:
            return self.loop
        if "FROM fleet_tasks WHERE id=$1" in query:
            return {"id": 9, "input": "test", "agent_name": "worker-01", "parent_task_id": None}
        if "SELECT status, task_id FROM fleet_task_loops" in query:
            return {"status": "running", "task_id": 9}
        raise AssertionError(query)

    async def fetchval(self, query, *_args):
        if "SELECT pool_status FROM fleet_tasks" in query:
            return "open"
        raise AssertionError(query)


class FakeResponse:
    status_code = 200
    text = ""

    def json(self):
        return {"messages": [{"type": "ai", "content": "done"}]}


class FakeClient:
    def __init__(self, outer):
        self.outer = outer

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, _path, json):
        self.outer.body = json
        return FakeResponse()


class RunBodyTests(unittest.IsolatedAsyncioTestCase):
    async def run_loop(self, profile):
        conn = RunConnection(profile)

        class ClientFactory:
            body = None

            def __call__(self, **_kwargs):
                return FakeClient(self)

        client = ClientFactory()
        with (
            patch("app.dispatcher.pool", AsyncMock(return_value=FakeAcquire(conn))),
            patch("app.dispatcher._chain", AsyncMock(return_value=[(9, "worker-01")])),
            patch("app.dispatcher.httpx.AsyncClient", client),
            patch("app.pool.deliver_loop", AsyncMock()),
            patch("app.dispatcher.audit", AsyncMock()),
        ):
            await dispatcher._run_loop(7)
        return client.body

    async def test_selected_profile_is_sent_as_run_context(self):
        body = await self.run_loop("reasoner")
        self.assertEqual(body["context"], {"model_name": "reasoner"})

    async def test_legacy_loop_without_profile_keeps_legacy_request(self):
        body = await self.run_loop(None)
        self.assertNotIn("context", body)


class ArtifactLinkTests(unittest.TestCase):
    def test_default_link_is_same_origin_and_deployment_neutral(self):
        with patch.dict("os.environ", {}, clear=True):
            links = _extract_artifacts(
                {"artifacts": ["/mnt/user-data/outputs/result.md"]},
                "4c6a2c52-16f2-4b0d-9882-4f826ea58f6d",
            )
        self.assertEqual(links, [
            "result.md: /api/console/threads/4c6a2c52-16f2-4b0d-9882-4f826ea58f6d"
            "/artifacts/mnt/user-data/outputs/result.md"
        ])
        self.assertNotIn("127.0.0.1", links[0])
        self.assertNotIn("localhost", links[0])

    def test_explicit_public_base_remains_supported(self):
        with patch.dict("os.environ", {"FLEET_ARTIFACT_LINK_BASE": "https://fleet.example.com/"}):
            links = _extract_artifacts(
                {"artifacts": ["/mnt/user-data/outputs/result.md"]},
                "4c6a2c52-16f2-4b0d-9882-4f826ea58f6d",
            )
        self.assertTrue(links[0].startswith("result.md: https://fleet.example.com/api/"))


if __name__ == "__main__":
    unittest.main()
