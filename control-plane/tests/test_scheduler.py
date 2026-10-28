"""cron 执行者归属与 ADR-0012 原子投递回归（不依赖 PG）。"""

import unittest
from datetime import datetime
from unittest.mock import ANY, AsyncMock, call, patch
from zoneinfo import ZoneInfo

from app import scheduler
from app.pool import PoolError


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
        self.execute = AsyncMock()
        self.tx = FakeTransaction(events)

    def transaction(self):
        return self.tx

    async def fetchval(self, sql, *_args):
        if "pg_advisory_xact_lock" in sql:
            return None
        return datetime(2000, 1, 1, tzinfo=ZoneInfo("Asia/Shanghai"))


class FakePool:
    def __init__(self, events):
        self.conn = FakeConnection(events)

    def acquire(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return None


class CronSpecTests(unittest.TestCase):
    def test_orchestrator_defaults_to_none_and_can_be_explicit(self):
        specs = scheduler._cron_specs({"triggers": {"cron": [
            "37 8 * * 1-5",
            {"cron": "0 9 * * *", "prompt": "日报", "orchestrator": "fleet-cmd-01"},
        ]}})

        self.assertIsNone(specs[0]["orchestrator"])
        self.assertEqual(specs[1]["orchestrator"], "fleet-cmd-01")


class CronClaimantTests(unittest.IsolatedAsyncioTestCase):
    async def run_sync(self, docs, *, default_orchestrator=None, task_ids=None, claim_error=None):
        ids = task_ids or [101, 102]
        events = []
        create_task = AsyncMock(side_effect=[
            ({"id": i, "pool_status": "open"}, True) for i in ids
        ])
        claim_loop = AsyncMock(
            side_effect=claim_error or [
                ({"id": i + 1000, "status": "claimed"}, True) for i in ids
            ]
        )
        enqueue = AsyncMock(side_effect=lambda *_args, **_kwargs: events.append("enqueue"))
        audit = AsyncMock()
        with (
            patch("app.scheduler._retire_gateway_crons", AsyncMock(return_value=0)),
            patch("app.scheduler.load_all_yaml", return_value=docs),
            patch("app.scheduler.pool", AsyncMock(return_value=FakePool(events))),
            patch("app.scheduler.DEFAULT_ORCHESTRATOR", default_orchestrator),
            patch("app.pool._create_task_with_conn", create_task),
            patch("app.pool._claim_loop_with_conn", claim_loop),
            patch("app.scheduler.enqueue_loop_run", enqueue),
            patch("app.scheduler.audit", audit),
        ):
            result = await scheduler.sync()
        return result, create_task, claim_loop, enqueue, audit, events

    async def test_default_claims_owner_and_explicit_cron_claims_orchestrator(self):
        docs = {
            "ar-reconciliation-01": {"triggers": {"cron": [
                {"cron": "* * * * *", "prompt": "对账"},
            ]}},
            "report-01": {"triggers": {"cron": [
                {"cron": "* * * * *", "prompt": "日报", "orchestrator": "fleet-cmd-01"},
            ]}},
        }

        result, create_task, claim_loop, enqueue, audit, events = await self.run_sync(docs)

        self.assertEqual(result["fired"], [101, 102])
        self.assertEqual(create_task.await_count, 2)
        self.assertTrue(all(c.kwargs["source"] == "cron" for c in create_task.await_args_list))
        self.assertEqual(claim_loop.await_args_list, [
            call(ANY, 101, "agent", "ar-reconciliation-01", created_by="scheduler", idempotent=True),
            call(ANY, 102, "agent", "fleet-cmd-01", created_by="scheduler", idempotent=True),
        ])
        self.assertEqual(enqueue.await_args_list, [call(1101), call(1102)])
        self.assertLess(events.index("commit"), events.index("enqueue"))
        cron_audits = [c for c in audit.await_args_list if c.args[0] == "cron.fired"]
        self.assertEqual(cron_audits[0].kwargs["agent_name"], "ar-reconciliation-01")
        self.assertEqual(cron_audits[0].kwargs["detail"]["claimed_by"], "ar-reconciliation-01")
        self.assertEqual(cron_audits[1].kwargs["agent_name"], "report-01")
        self.assertEqual(cron_audits[1].kwargs["detail"]["claimed_by"], "fleet-cmd-01")

    async def test_explicit_global_orchestrator_is_used_and_cron_override_wins(self):
        docs = {
            "owner-01": {"triggers": {"cron": [{"cron": "* * * * *"}]}},
            "owner-02": {"triggers": {"cron": [
                {"cron": "* * * * *", "orchestrator": "fleet-cmd-01"},
            ]}},
        }

        _, _, claim_loop, _, _, _ = await self.run_sync(
            docs, default_orchestrator="global-commander")

        self.assertEqual(claim_loop.await_args_list, [
            call(ANY, 101, "agent", "global-commander", created_by="scheduler", idempotent=True),
            call(ANY, 102, "agent", "fleet-cmd-01", created_by="scheduler", idempotent=True),
        ])

    async def test_claim_failure_rolls_back_without_fired_marker_or_enqueue(self):
        docs = {"owner-01": {"triggers": {"cron": [{"cron": "* * * * *"}]}}}

        result, _, _, enqueue, audit, events = await self.run_sync(
            docs, task_ids=[101], claim_error=PoolError(409, "agent hibernated"))

        self.assertEqual(result["fired"], [])
        self.assertIn("rollback", events)
        self.assertNotIn("enqueue", events)
        enqueue.assert_not_awaited()
        audit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
