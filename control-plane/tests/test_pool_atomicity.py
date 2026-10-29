"""ADR-0012：认领事务化、认领者唯一与交付／打回状态守卫。"""

import asyncio
import os
import unittest
import uuid
from unittest.mock import AsyncMock, patch

import asyncpg

from app import db as dbmod
from app import dispatcher
from app import pool as poolmod


class FakeTransaction:
    def __init__(self):
        self.entered = 0

    async def __aenter__(self):
        self.entered += 1

    async def __aexit__(self, *_args):
        return None


class FakeConnection:
    def __init__(self, *, fetchrows=(), fetchvals=()):
        self.fetch = AsyncMock(return_value=[])
        self.fetchrow = AsyncMock(side_effect=fetchrows)
        self.fetchval = AsyncMock(side_effect=fetchvals)
        self.execute = AsyncMock()
        self.tx = FakeTransaction()

    def transaction(self):
        return self.tx


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return None


class ClaimTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def run_claim(self, conn, claimant_type="agent", claimant="agent-1"):
        with (
            patch("app.pool.pool", AsyncMock(return_value=FakePool(conn))),
            patch("app.pool.audit", AsyncMock()),
            patch("app.pool.enqueue_loop_run", AsyncMock()),
            patch("app.models.resolve_profile", AsyncMock(
                return_value=("deepseek-chat", {"profile": "deepseek-chat"}, "system_default"))),
        ):
            return await poolmod.claim_loop(
                11, claimant_type, claimant, created_by="test"
            )

    async def test_claim_runs_inside_explicit_transaction(self):
        conn = FakeConnection(
            fetchrows=[
                {"id": 11, "pool_status": "open"},
                None,
                {"id": 21, "task_id": 11, "status": "claimed"},
            ],
            fetchvals=["registered", 0],
        )

        loop = await self.run_claim(conn)

        self.assertEqual(loop["id"], 21)
        self.assertEqual(conn.tx.entered, 1)

    async def test_duplicate_claimant_is_rejected(self):
        conn = FakeConnection(
            fetchrows=[{"id": 11, "pool_status": "open"}, {"id": 21}],
            fetchvals=["registered"],
        )

        with self.assertRaises(poolmod.PoolError) as caught:
            await self.run_claim(conn)

        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(conn.fetchrow.await_count, 2)

    async def test_human_claim_is_not_blocked_by_three_agent_loops(self):
        conn = FakeConnection(
            fetchrows=[
                {"id": 11, "pool_status": "open"},
                None,
                {"id": 22, "task_id": 11, "status": "claimed"},
            ],
            fetchvals=[3],
        )

        loop = await self.run_claim(conn, "human", "employee-1")

        self.assertEqual(loop["id"], 22)
        self.assertEqual(conn.tx.entered, 1)


class StateGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_running_loop_cannot_be_returned(self):
        conn = FakeConnection(fetchrows=[None, {"status": "running"}])
        with patch("app.pool.pool", AsyncMock(return_value=FakePool(conn))):
            with self.assertRaises(poolmod.PoolError) as caught:
                await poolmod.return_loop(21, "修正", author="reviewer")

        self.assertEqual(caught.exception.status, 409)
        self.assertIn("status='delivered'", conn.fetchrow.await_args_list[0].args[0])

    async def test_archived_loop_cannot_be_delivered(self):
        conn = FakeConnection(fetchrows=[None, {"task_id": 11, "status": "archived"}])
        with patch("app.pool.pool", AsyncMock(return_value=FakePool(conn))):
            with self.assertRaises(poolmod.PoolError) as caught:
                await poolmod.deliver_loop(21, "迟到结果")

        self.assertEqual(caught.exception.status, 409)
        conn.execute.assert_not_awaited()

    async def test_dispatcher_claim_to_running_uses_transaction(self):
        conn = FakeConnection(fetchrows=[None])
        with patch("app.dispatcher.pool", AsyncMock(return_value=FakePool(conn))):
            await dispatcher._run_loop(21)

        self.assertEqual(conn.tx.entered, 1)

    async def test_reset_does_not_revive_archived_loop(self):
        conn = FakeConnection()
        with patch("app.dispatcher.pool", AsyncMock(return_value=FakePool(conn))):
            await dispatcher._reset_loop(21, "late failure")

        sql = conn.execute.await_args.args[0]
        self.assertIn("status IN ('claimed','running','returned')", sql)


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_clean_database_gets_unique_index(self):
        conn = FakeConnection()
        with patch("app.db.pool", AsyncMock(return_value=FakePool(conn))):
            await dbmod.init_db()

        statements = [call.args[0] for call in conn.execute.await_args_list]
        self.assertTrue(any("CREATE UNIQUE INDEX" in sql for sql in statements))

    async def test_historical_duplicates_warn_without_blocking_startup(self):
        conn = FakeConnection()
        conn.fetch.return_value = [{
            "task_id": 11, "claimant_type": "human", "claimant": "me", "n": 2,
        }]
        with (
            patch("app.db.pool", AsyncMock(return_value=FakePool(conn))),
            patch("app.db.logger.warning") as warning,
        ):
            await dbmod.init_db()

        statements = [call.args[0] for call in conn.execute.await_args_list]
        self.assertFalse(any("CREATE UNIQUE INDEX" in sql for sql in statements))
        warning.assert_called_once()


@unittest.skipUnless(os.environ.get("FLEET_TEST_PG_DSN"), "未配置 FLEET_TEST_PG_DSN")
class PostgresConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schema = f"test_pool_{uuid.uuid4().hex}"
        self.admin = await asyncpg.connect(os.environ["FLEET_TEST_PG_DSN"])
        await self.admin.execute(f'CREATE SCHEMA "{self.schema}"')
        self.db = await asyncpg.create_pool(
            os.environ["FLEET_TEST_PG_DSN"],
            min_size=1,
            max_size=30,
            server_settings={"search_path": self.schema},
        )
        async with self.db.acquire() as conn:
            await conn.execute("""
                CREATE TABLE fleet_agents (name TEXT PRIMARY KEY, status TEXT NOT NULL);
                CREATE TABLE fleet_tasks (
                    id BIGSERIAL PRIMARY KEY,
                    agent_name TEXT NOT NULL DEFAULT 'unassigned',
                    input TEXT NOT NULL DEFAULT 'test',
                    pool_status TEXT NOT NULL DEFAULT 'open',
                    model_profile TEXT,
                    subagent_model_policy TEXT NOT NULL DEFAULT 'auto'
                );
                CREATE TABLE fleet_task_loops (
                    id BIGSERIAL PRIMARY KEY,
                    task_id BIGINT NOT NULL,
                    claimant_type TEXT NOT NULL,
                    claimant TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'claimed',
                    version INTEGER NOT NULL DEFAULT 1,
                    deliverable TEXT,
                    tokens BIGINT,
                    thread_id TEXT,
                    delivered_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    model_profile TEXT,
                    model_snapshot JSONB,
                    model_selected_by TEXT,
                    UNIQUE(task_id, claimant_type, claimant)
                );
            """)
            await conn.executemany(
                "INSERT INTO fleet_agents VALUES ($1, 'registered')",
                [(f"agent-{i}",) for i in range(20)],
            )
        self.patches = [
            patch("app.pool.pool", AsyncMock(return_value=self.db)),
            patch("app.pool.audit", AsyncMock()),
            patch("app.pool.enqueue_loop_run", AsyncMock()),
            patch("app.models.resolve_profile", AsyncMock(
                return_value=("deepseek-chat", {"profile": "deepseek-chat"}, "system_default"))),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in self.patches:
            item.stop()
        await self.db.close()
        await self.admin.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
        await self.admin.close()

    async def new_task(self):
        async with self.db.acquire() as conn:
            return await conn.fetchval("INSERT INTO fleet_tasks DEFAULT VALUES RETURNING id")

    async def test_concurrent_claims_never_exceed_three(self):
        task_id = await self.new_task()

        results = await asyncio.gather(*[
            poolmod.claim_loop(task_id, "agent", f"agent-{i}", created_by="test")
            for i in range(20)
        ], return_exceptions=True)

        claimed = [item for item in results if not isinstance(item, Exception)]
        rejected = [item for item in results if isinstance(item, poolmod.PoolError)]
        self.assertEqual(len(claimed), 3)
        self.assertEqual(len(rejected), 17)
        self.assertTrue(all(item.status == 409 for item in rejected))
        async with self.db.acquire() as conn:
            count = await conn.fetchval(
                "SELECT count(*) FROM fleet_task_loops WHERE task_id=$1", task_id
            )
        self.assertEqual(count, 3)

    async def test_same_claimant_gets_only_one_loop(self):
        task_id = await self.new_task()

        results = await asyncio.gather(*[
            poolmod.claim_loop(task_id, "agent", "agent-1", created_by="test")
            for _ in range(20)
        ], return_exceptions=True)

        claimed = [item for item in results if not isinstance(item, Exception)]
        self.assertEqual(len(claimed), 1)
        async with self.db.acquire() as conn:
            count = await conn.fetchval(
                "SELECT count(*) FROM fleet_task_loops WHERE task_id=$1 AND claimant='agent-1'",
                task_id,
            )
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
