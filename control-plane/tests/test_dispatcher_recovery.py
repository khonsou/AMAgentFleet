"""ADR-0012：启动恢复、执行心跳、reaper 窗口与打回意见持久化。"""

import asyncio
import os
import unittest
import uuid
from unittest.mock import AsyncMock, patch

import asyncpg

from app import dispatcher
from app import pool as poolmod


class ReaperWindowTests(unittest.TestCase):
    def test_reaper_window_is_at_least_twice_run_timeout(self):
        self.assertGreaterEqual(dispatcher.REAPER_TIMEOUT, 2 * dispatcher.RUN_TIMEOUT)


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.text = "gateway rejected" if status_code != 200 else ""
        self._payload = payload if payload is not None else {"status": "success", "messages": []}

    def json(self):
        return self._payload


class FakeClient:
    post_impl = None

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, *args, **kwargs):
        return await type(self).post_impl(*args, **kwargs)


@unittest.skipUnless(os.environ.get("FLEET_TEST_PG_DSN"), "未配置 FLEET_TEST_PG_DSN")
class PostgresRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await dispatcher.shutdown()
        dispatcher._QUEUE = asyncio.Queue()
        dispatcher.set_background_enabled(True)
        self.schema = f"test_recovery_{uuid.uuid4().hex}"
        dsn = os.environ["FLEET_TEST_PG_DSN"]
        self.admin = await asyncpg.connect(dsn)
        await self.admin.execute(f'CREATE SCHEMA "{self.schema}"')
        self.db = await asyncpg.create_pool(
            dsn, min_size=1, max_size=10,
            server_settings={"search_path": self.schema},
        )
        async with self.db.acquire() as conn:
            await conn.execute("""
                CREATE TABLE fleet_agents (name TEXT PRIMARY KEY, status TEXT NOT NULL);
                CREATE TABLE fleet_tasks (
                    id BIGSERIAL PRIMARY KEY,
                    agent_name TEXT NOT NULL DEFAULT 'agent-1',
                    input TEXT NOT NULL DEFAULT '原始任务',
                    parent_task_id BIGINT,
                    pool_status TEXT NOT NULL DEFAULT 'open',
                    status TEXT NOT NULL DEFAULT 'queued',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                CREATE TABLE fleet_task_loops (
                    id BIGSERIAL PRIMARY KEY,
                    task_id BIGINT NOT NULL,
                    claimant_type TEXT NOT NULL DEFAULT 'agent',
                    claimant TEXT NOT NULL DEFAULT 'agent-1',
                    created_by TEXT NOT NULL DEFAULT 'test',
                    status TEXT NOT NULL DEFAULT 'claimed',
                    version INTEGER NOT NULL DEFAULT 1,
                    error TEXT,
                    thread_id TEXT,
                    resume_comment TEXT,
                    deliverable TEXT,
                    tokens BIGINT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    delivered_at TIMESTAMPTZ
                );
                CREATE TABLE fleet_task_comments (
                    id BIGSERIAL PRIMARY KEY,
                    task_id BIGINT NOT NULL,
                    loop_id BIGINT,
                    author TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                INSERT INTO fleet_agents VALUES ('agent-1', 'registered');
            """)
        self.patches = [
            patch("app.dispatcher.pool", AsyncMock(return_value=self.db)),
            patch("app.pool.pool", AsyncMock(return_value=self.db)),
            patch("app.dispatcher.audit", AsyncMock()),
            patch("app.pool.audit", AsyncMock()),
            patch("app.pool.enqueue_loop_run", AsyncMock()),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        await dispatcher.shutdown()
        for item in self.patches:
            item.stop()
        await self.db.close()
        await self.admin.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
        await self.admin.close()

    async def new_loop(self, *, status="claimed", claimant_type="agent", resume_comment=None):
        async with self.db.acquire() as conn:
            task_id = await conn.fetchval("INSERT INTO fleet_tasks DEFAULT VALUES RETURNING id")
            loop_id = await conn.fetchval("""
                INSERT INTO fleet_task_loops (task_id, claimant_type, status, resume_comment)
                VALUES ($1, $2, $3, $4) RETURNING id
            """, task_id, claimant_type, status, resume_comment)
        return task_id, loop_id

    async def loop_row(self, loop_id):
        async with self.db.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM fleet_task_loops WHERE id=$1", loop_id)

    async def test_startup_recovers_committed_agent_loops_only(self):
        _, claimed = await self.new_loop(status="claimed")
        _, returned = await self.new_loop(status="returned", resume_comment="保留意见")
        await self.new_loop(status="delivered")
        await self.new_loop(status="claimed", claimant_type="human")

        with patch("app.dispatcher._start_consumers") as start:
            recovered = await dispatcher.recover_claimed()

        self.assertEqual(recovered, 2)
        self.assertEqual([dispatcher._QUEUE.get_nowait(), dispatcher._QUEUE.get_nowait()],
                         [claimed, returned])
        start.assert_called_once_with()
        self.assertEqual((await self.loop_row(returned))["resume_comment"], "保留意见")

    async def test_resume_comment_survives_rejection_and_clears_after_delivery(self):
        _, loop_id = await self.new_loop(status="delivered")
        await poolmod.return_loop(loop_id, "请补充证据", author="reviewer")
        self.assertEqual((await self.loop_row(loop_id))["resume_comment"], "请补充证据")

        # 模拟进程重启：内存队列全部丢失，只保留数据库中的 Loop 与意见。
        dispatcher._QUEUE = asyncio.Queue()
        bodies = []

        async def rejected(_path, json):
            bodies.append(json)
            return FakeResponse(status_code=503)

        with patch("app.dispatcher.httpx.AsyncClient", FakeClient), \
                patch.object(FakeClient, "post_impl", new=rejected):
            await dispatcher._run_loop(loop_id)
        row = await self.loop_row(loop_id)
        self.assertEqual(row["status"], "claimed")
        self.assertEqual(row["resume_comment"], "请补充证据")

        async def succeeded(_path, json):
            bodies.append(json)
            return FakeResponse()

        with patch("app.dispatcher.httpx.AsyncClient", FakeClient), \
                patch.object(FakeClient, "post_impl", new=succeeded):
            await dispatcher._run_loop(loop_id)
        row = await self.loop_row(loop_id)
        self.assertEqual(row["status"], "delivered")
        self.assertIsNone(row["resume_comment"])
        self.assertEqual(len(bodies), 2)
        for body in bodies:
            content = body["input"]["messages"][0]["content"]
            self.assertIn("【验收退回意见】请补充证据", content)

    async def test_heartbeat_refreshes_running_loop_before_gateway_returns(self):
        _, loop_id = await self.new_loop()
        gateway_release = asyncio.Event()
        gateway_started = asyncio.Event()

        async def blocked(_path, json):
            gateway_started.set()
            await gateway_release.wait()
            return FakeResponse()

        with patch("app.dispatcher.httpx.AsyncClient", FakeClient), \
                patch.object(FakeClient, "post_impl", new=blocked), \
                patch("app.dispatcher.HEARTBEAT_INTERVAL", 0.01):
            running = asyncio.create_task(dispatcher._run_loop(loop_id))
            await asyncio.wait_for(gateway_started.wait(), timeout=1)
            before = (await self.loop_row(loop_id))["updated_at"]
            await asyncio.sleep(0.04)
            during = (await self.loop_row(loop_id))["updated_at"]
            self.assertGreater(during, before)
            self.assertEqual((await self.loop_row(loop_id))["status"], "running")
            gateway_release.set()
            await asyncio.wait_for(running, timeout=1)

        self.assertEqual((await self.loop_row(loop_id))["status"], "delivered")


if __name__ == "__main__":
    unittest.main()
