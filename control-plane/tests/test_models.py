"""ADR-0028：实例选模、继承、白名单与 schema 回归（不依赖 PG/Gateway）。"""

import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import models, pool, scheduler
from app.db import SCHEMA


PROFILES = [
    {"name": "deepseek-chat", "model": "deepseek-chat", "display_name": "DeepSeek Chat"},
    {"name": "reasoner", "model": "deepseek-reasoner", "display_name": "DeepSeek Reasoner"},
    {"name": "qwen", "model": "qwen3-max", "display_name": "Qwen Max"},
]


class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return None


class FakeTransaction:
    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class TaskConnection(FakeTransaction):
    def __init__(self, parent=None):
        self.parent = parent
        self.inserts = []

    async def fetchrow(self, query, *args):
        if "SELECT model_profile, subagent_model_policy" in query:
            return self.parent
        if "INSERT INTO fleet_tasks" in query:
            self.inserts.append(args)
            return {
                "id": 42, "agent_name": args[0] or "unassigned", "input": args[1],
                "model_profile": args[5], "subagent_model_policy": args[6],
                "pool_status": "open",
            }
        raise AssertionError(query)


class LoopConnection(FakeTransaction):
    def __init__(self, task):
        self.task = task
        self.inserts = []
        self.execute = AsyncMock()

    async def fetchval(self, query, *_args):
        if "SELECT status FROM fleet_agents" in query:
            return "registered"
        if "SELECT count(*) FROM fleet_task_loops" in query:
            return 0
        raise AssertionError(query)

    async def fetchrow(self, query, *_args):
        if "SELECT * FROM fleet_tasks" in query:
            return self.task
        if "SELECT * FROM fleet_task_loops" in query:
            return None
        if "INSERT INTO fleet_task_loops" in query:
            self.inserts.append(_args)
            return {"id": 7, "task_id": self.task["id"], "status": "claimed"}
        raise AssertionError(query)


class ResolveProfileTests(unittest.IsolatedAsyncioTestCase):
    async def resolve(self, requested, *, default=None, allowed=None, selected_by="human"):
        with (
            patch("app.models._agent_policy", return_value=(default, allowed)),
            patch("app.models.list_profiles", AsyncMock(return_value=PROFILES)),
        ):
            return await models.resolve_profile("worker-01", requested, selected_by=selected_by)

    async def test_same_agent_instances_can_use_different_profiles(self):
        first, snapshot, _ = await self.resolve("reasoner")
        second, _, _ = await self.resolve("qwen", selected_by="fleet-cmd-01")
        self.assertEqual((first, second), ("reasoner", "qwen"))
        self.assertEqual(snapshot, {
            "profile": "reasoner", "model": "deepseek-reasoner",
            "display_name": "DeepSeek Reasoner", "source": "human",
        })
        self.assertNotIn("api_key", json.dumps(snapshot))
        self.assertNotIn("base_url", json.dumps(snapshot))

    async def test_explicit_then_agent_default_then_system_default(self):
        self.assertEqual((await self.resolve(None, default="qwen"))[0], "qwen")
        profile, _, source = await self.resolve(None)
        self.assertEqual((profile, source), ("deepseek-chat", "system_default"))

    async def test_unknown_profile_and_agent_whitelist_are_rejected(self):
        with self.assertRaises(models.ModelError) as unknown:
            await self.resolve("missing")
        self.assertEqual(unknown.exception.status, 422)
        with self.assertRaises(models.ModelError) as denied:
            await self.resolve("qwen", allowed=["deepseek-chat", "reasoner"])
        self.assertEqual(denied.exception.status, 403)

    async def test_public_catalog_never_forwards_provider_secrets(self):
        class Response:
            status_code = 200

            def json(self):
                return {"models": [{**PROFILES[0], "base_url": "https://secret.example", "api_key": "secret"}]}

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, _path):
                return Response()

        with patch("app.models.httpx.AsyncClient", return_value=Client()):
            result = await models.list_profiles()
        self.assertEqual(result[0]["name"], "deepseek-chat")
        self.assertNotIn("api_key", result[0])
        self.assertNotIn("base_url", result[0])


class TaskPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def create(self, conn, **kwargs):
        with (
            patch("app.pool.pool", AsyncMock(return_value=FakeAcquire(conn))),
            patch("app.pool.audit", AsyncMock()),
        ):
            return await pool.create_task("test", **kwargs)

    async def test_inherit_propagates_parent_profile(self):
        conn = TaskConnection({"model_profile": "reasoner", "subagent_model_policy": "inherit"})
        task = await self.create(conn, parent_task_id=1)
        self.assertEqual(task["model_profile"], "reasoner")
        self.assertEqual(task["subagent_model_policy"], "inherit")

    async def test_inherit_conflict_fails_before_insert(self):
        conn = TaskConnection({"model_profile": "reasoner", "subagent_model_policy": "inherit"})
        with self.assertRaises(pool.PoolError) as denied:
            await self.create(conn, parent_task_id=1, model_profile="qwen")
        self.assertEqual(denied.exception.status, 403)
        self.assertEqual(conn.inserts, [])

    async def test_root_inherit_requires_explicit_profile(self):
        conn = TaskConnection()
        with self.assertRaises(pool.PoolError) as invalid:
            await self.create(conn, subagent_model_policy="inherit")
        self.assertEqual(invalid.exception.status, 422)
        self.assertEqual(conn.inserts, [])

    async def test_missing_parent_and_unresolved_inherit_fail_before_insert(self):
        missing = TaskConnection(parent=None)
        with self.assertRaises(pool.PoolError) as absent:
            await self.create(missing, parent_task_id=999)
        self.assertEqual(absent.exception.status, 404)
        self.assertEqual(missing.inserts, [])

        no_profile = TaskConnection({"model_profile": None, "subagent_model_policy": "inherit"})
        with self.assertRaises(pool.PoolError) as unresolved:
            await self.create(no_profile, parent_task_id=1)
        self.assertEqual(unresolved.exception.status, 422)
        self.assertEqual(no_profile.inserts, [])

    async def test_legacy_auto_request_keeps_null_profile(self):
        conn = TaskConnection()
        task = await self.create(conn)
        self.assertIsNone(task["model_profile"])
        self.assertEqual(task["subagent_model_policy"], "auto")


class LoopSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def claim(self, resolver):
        task = {"id": 9, "pool_status": "open", "model_profile": "reasoner"}
        conn = LoopConnection(task)
        with (
            patch("app.pool.pool", AsyncMock(return_value=FakeAcquire(conn))),
            patch("app.models.resolve_profile", resolver),
            patch("app.pool.enqueue_loop_run", AsyncMock()) as enqueue,
            patch("app.pool.audit", AsyncMock()),
        ):
            loop = await pool.claim_loop(9, "agent", "worker-01", created_by="human")
        return conn, loop, enqueue

    async def test_loop_persists_profile_snapshot_before_enqueue(self):
        resolver = AsyncMock(return_value=("reasoner", {"profile": "reasoner", "model": "deepseek-reasoner"}, "task"))
        conn, loop, enqueue = await self.claim(resolver)
        self.assertEqual(loop["id"], 7)
        self.assertEqual(conn.inserts[0][4], "reasoner")
        self.assertEqual(json.loads(conn.inserts[0][5])["model"], "deepseek-reasoner")
        enqueue.assert_awaited_once_with(7)

    async def test_resolution_error_leaves_no_loop_or_queue_entry(self):
        task = {"id": 9, "pool_status": "open", "model_profile": "reasoner"}
        conn = LoopConnection(task)
        with (
            patch("app.pool.pool", AsyncMock(return_value=FakeAcquire(conn))),
            patch("app.models.resolve_profile", AsyncMock(side_effect=models.ModelError(422, "unknown"))),
            patch("app.pool.enqueue_loop_run", AsyncMock()) as enqueue,
            patch("app.pool.audit", AsyncMock()),
        ):
            with self.assertRaises(pool.PoolError):
                await pool.claim_loop(9, "agent", "worker-01", created_by="human")
        self.assertEqual(conn.inserts, [])
        enqueue.assert_not_awaited()


class SchemaAndCronTests(unittest.TestCase):
    def test_loop_table_exists_before_its_migrations(self):
        self.assertLess(SCHEMA.index("CREATE TABLE IF NOT EXISTS fleet_task_loops"),
                        SCHEMA.index("ALTER TABLE fleet_task_loops"))

    def test_cron_preserves_model_fields_and_legacy_defaults(self):
        legacy, selected = scheduler._cron_specs({"triggers": {"cron": [
            "* * * * *",
            {"cron": "* * * * *", "model_profile": "reasoner", "subagent_model_policy": "inherit"},
        ]}})
        self.assertIsNone(legacy["model_profile"])
        self.assertEqual(legacy["subagent_model_policy"], "auto")
        self.assertEqual(selected["model_profile"], "reasoner")
        self.assertEqual(selected["subagent_model_policy"], "inherit")

    def test_protocol_registers_model_selection_and_human_only_key_management(self):
        protocol = json.loads((Path(__file__).parents[2] / "fleet-ops-protocol.json").read_text())
        operations = {item["id"]: item for item in protocol["operations"]}
        self.assertEqual(operations["models.list"]["path"], "/api/models")
        self.assertIn("model_profile", operations["tasks.create"]["body"])
        self.assertIn("model_profile", operations["loops.claim"]["body"])
        for operation in ("models.managed_list", "models.managed_save", "models.managed_test"):
            self.assertEqual(operations[operation]["confirmation"], "human_only")


if __name__ == "__main__":
    unittest.main()
