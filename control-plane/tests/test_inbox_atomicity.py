"""ADR-0012：cron／Inbox 原子投递与幂等唤醒回归。"""

import os
import unittest
import uuid
from datetime import datetime
from unittest.mock import ANY, AsyncMock, patch
from zoneinfo import ZoneInfo

import asyncpg

from app import inbox, scheduler
from app import pool as poolmod


class FakeTransaction:
    def __init__(self, events):
        self.events = events

    async def __aenter__(self):
        self.events.append("begin")

    async def __aexit__(self, exc_type, *_args):
        self.events.append("rollback" if exc_type else "commit")


class FakeConnection:
    def __init__(self, events):
        self.events = events
        self.fetch = AsyncMock(return_value=[{"id": 7, "payload": "唤醒"}])
        self.tx = FakeTransaction(events)

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


class InboxTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def run_flush(self, events, claim_error=None):
        conn = FakeConnection(events)
        create = AsyncMock(return_value=({"id": 101, "pool_status": "open"}, True))
        claim = AsyncMock(
            side_effect=claim_error or [({"id": 201, "status": "claimed"}, True)]
        )
        enqueue = AsyncMock(side_effect=lambda *_args: events.append("enqueue"))
        audit = AsyncMock()
        with (
            patch("app.inbox.pool", AsyncMock(return_value=FakePool(conn))),
            patch("app.pool._create_task_with_conn", create),
            patch("app.pool._claim_loop_with_conn", claim),
            patch("app.inbox.enqueue_loop_run", enqueue),
            patch("app.inbox.audit", audit),
        ):
            result = await inbox.flush_agent("agent-1")
        return result, create, claim, enqueue, audit

    async def test_enqueue_happens_after_commit(self):
        events = []

        result, _, claim, enqueue, _ = await self.run_flush(events)

        self.assertEqual(result, 1)
        self.assertEqual(events, ["begin", "commit", "enqueue"])
        claim.assert_awaited_once_with(
            ANY, 101, "agent", "agent-1", created_by="dispatcher", idempotent=True)
        enqueue.assert_awaited_once_with(201)

    async def test_claim_failure_rolls_back_inbox_status(self):
        events = []

        with self.assertRaises(poolmod.PoolError):
            await self.run_flush(events, poolmod.PoolError(409, "hibernated"))

        self.assertEqual(events, ["begin", "rollback"])


@unittest.skipUnless(os.environ.get("FLEET_TEST_PG_DSN"), "未配置 FLEET_TEST_PG_DSN")
class PostgresTriggerAtomicityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schema = f"test_trigger_{uuid.uuid4().hex}"
        dsn = os.environ["FLEET_TEST_PG_DSN"]
        self.admin = await asyncpg.connect(dsn)
        await self.admin.execute(f'CREATE SCHEMA "{self.schema}"')
        self.db = await asyncpg.create_pool(
            dsn, min_size=1, max_size=20,
            server_settings={"search_path": self.schema},
        )
        async with self.db.acquire() as conn:
            await conn.execute("""
                CREATE TABLE fleet_agents (name TEXT PRIMARY KEY, status TEXT NOT NULL);
                CREATE TABLE fleet_tasks (
                    id BIGSERIAL PRIMARY KEY,
                    agent_name TEXT NOT NULL DEFAULT 'unassigned',
                    input TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'web',
                    parent_task_id BIGINT,
                    idempotency_key TEXT UNIQUE,
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
                    model_profile TEXT,
                    model_snapshot JSONB,
                    model_selected_by TEXT,
                    UNIQUE(task_id, claimant_type, claimant)
                );
                CREATE TABLE fleet_inbox (
                    id BIGSERIAL PRIMARY KEY,
                    agent_name TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'agent',
                    status TEXT NOT NULL DEFAULT 'pending',
                    dispatched_at TIMESTAMPTZ
                );
                CREATE TABLE fleet_cron_fires (
                    agent_name TEXT NOT NULL,
                    cron_idx INTEGER NOT NULL,
                    last_fired_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY(agent_name, cron_idx)
                );
            """)
            await conn.execute("INSERT INTO fleet_agents VALUES ('agent-1', 'registered')")
            await conn.execute("INSERT INTO fleet_agents VALUES ('agent-2', 'hibernated')")
        self.enqueue = AsyncMock()
        self.patches = [
            patch("app.pool.pool", AsyncMock(return_value=self.db)),
            patch("app.inbox.pool", AsyncMock(return_value=self.db)),
            patch("app.scheduler.pool", AsyncMock(return_value=self.db)),
            patch("app.pool.audit", AsyncMock()),
            patch("app.inbox.audit", AsyncMock()),
            patch("app.scheduler.audit", AsyncMock()),
            patch("app.pool.enqueue_loop_run", self.enqueue),
            patch("app.inbox.enqueue_loop_run", self.enqueue),
            patch("app.scheduler.enqueue_loop_run", self.enqueue),
            patch("app.scheduler._retire_gateway_crons", AsyncMock(return_value=0)),
            patch("app.models.resolve_profile", AsyncMock(
                return_value=("deepseek-default", {"profile": "deepseek-default"}, "system_default"))),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in self.patches:
            item.stop()
        await self.db.close()
        await self.admin.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
        await self.admin.close()

    async def counts(self):
        async with self.db.acquire() as conn:
            tasks = await conn.fetchval("SELECT count(*) FROM fleet_tasks")
            loops = await conn.fetchval("SELECT count(*) FROM fleet_task_loops")
        return tasks, loops

    async def test_idempotent_create_requeues_existing_loop(self):
        first = await poolmod.create_task(
            "唤醒", source="inbox", assignee="agent-1",
            created_by="test", idempotency_key="same-message")
        self.enqueue.reset_mock()

        second = await poolmod.create_task(
            "唤醒", source="inbox", assignee="agent-1",
            created_by="test", idempotency_key="same-message")

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(await self.counts(), (1, 1))
        self.enqueue.assert_awaited_once()

    async def test_idempotent_create_claims_when_loop_is_missing(self):
        task = await poolmod.create_task(
            "唤醒", source="inbox", assignee="agent-1",
            created_by="test", idempotency_key="missing-loop")
        async with self.db.acquire() as conn:
            await conn.execute("DELETE FROM fleet_task_loops WHERE task_id=$1", task["id"])
        self.enqueue.reset_mock()

        replayed = await poolmod.create_task(
            "唤醒", source="inbox", assignee="agent-1",
            created_by="test", idempotency_key="missing-loop")

        self.assertEqual(replayed["id"], task["id"])
        self.assertEqual(await self.counts(), (1, 1))
        self.enqueue.assert_awaited_once()

    async def test_inbox_claim_failure_rolls_back_message_and_task(self):
        async with self.db.acquire() as conn:
            await conn.execute(
                "INSERT INTO fleet_inbox(agent_name, payload) VALUES ('agent-2', '唤醒')")

        with self.assertRaises(poolmod.PoolError):
            await inbox.flush_agent("agent-2")

        async with self.db.acquire() as conn:
            status = await conn.fetchval("SELECT status FROM fleet_inbox")
        self.assertEqual(status, "pending")
        self.assertEqual(await self.counts(), (0, 0))

    async def test_inbox_success_commits_message_task_and_loop(self):
        async with self.db.acquire() as conn:
            await conn.execute(
                "INSERT INTO fleet_inbox(agent_name, payload) VALUES ('agent-1', '唤醒')")

        self.assertEqual(await inbox.flush_agent("agent-1"), 1)

        async with self.db.acquire() as conn:
            status = await conn.fetchval("SELECT status FROM fleet_inbox")
        self.assertEqual(status, "dispatched")
        self.assertEqual(await self.counts(), (1, 1))
        self.enqueue.assert_awaited_once()

    async def prepare_cron(self, agent_name):
        old = datetime(2000, 1, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
        async with self.db.acquire() as conn:
            await conn.execute(
                "INSERT INTO fleet_cron_fires VALUES ($1, 0, $2)", agent_name, old)
        docs = {agent_name: {"triggers": {"cron": [{"cron": "* * * * *"}]}}}
        return old, patch("app.scheduler.load_all_yaml", return_value=docs)

    async def test_cron_claim_failure_rolls_back_task_and_fire_marker(self):
        old, docs = await self.prepare_cron("agent-2")
        with docs:
            result = await scheduler.sync()

        self.assertEqual(result["fired"], [])
        self.assertEqual(await self.counts(), (0, 0))
        async with self.db.acquire() as conn:
            last = await conn.fetchval(
                "SELECT last_fired_at FROM fleet_cron_fires WHERE agent_name='agent-2'")
        self.assertEqual(last, old)

    async def test_cron_second_sync_does_not_duplicate_task(self):
        _, docs = await self.prepare_cron("agent-1")
        with docs:
            first = await scheduler.sync()
            second = await scheduler.sync()

        self.assertEqual(len(first["fired"]), 1)
        self.assertEqual(second["fired"], [])
        self.assertEqual(await self.counts(), (1, 1))
        self.enqueue.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
