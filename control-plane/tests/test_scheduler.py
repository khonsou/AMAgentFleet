"""cron 执行者归属回归（ADR-0016，不依赖 PG）。"""

import unittest
from datetime import datetime
from unittest.mock import AsyncMock, call, patch
from zoneinfo import ZoneInfo

from app import scheduler


class FakeConnection:
    def __init__(self):
        self.execute = AsyncMock()

    async def fetchval(self, *_args):
        return datetime(2000, 1, 1, tzinfo=ZoneInfo("Asia/Shanghai"))


class FakePool:
    def __init__(self):
        self.conn = FakeConnection()

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
    async def run_sync(self, docs, *, default_orchestrator=None, task_ids=None):
        create_task = AsyncMock(side_effect=[{"id": i} for i in (task_ids or [101, 102])])
        claim_loop = AsyncMock()
        audit = AsyncMock()
        with (
            patch("app.scheduler._retire_gateway_crons", AsyncMock(return_value=0)),
            patch("app.scheduler.load_all_yaml", return_value=docs),
            patch("app.scheduler.pool", AsyncMock(return_value=FakePool())),
            patch("app.scheduler.DEFAULT_ORCHESTRATOR", default_orchestrator),
            patch("app.pool.create_task", create_task),
            patch("app.pool.claim_loop", claim_loop),
            patch("app.scheduler.audit", audit),
        ):
            result = await scheduler.sync()
        return result, create_task, claim_loop, audit

    async def test_default_claims_owner_and_explicit_cron_claims_orchestrator(self):
        docs = {
            "ar-reconciliation-01": {"triggers": {"cron": [
                {"cron": "* * * * *", "prompt": "对账"},
            ]}},
            "report-01": {"triggers": {"cron": [
                {"cron": "* * * * *", "prompt": "日报", "orchestrator": "fleet-cmd-01"},
            ]}},
        }

        result, create_task, claim_loop, audit = await self.run_sync(docs)

        self.assertEqual(result["fired"], [101, 102])
        self.assertEqual(create_task.await_count, 2)
        self.assertTrue(all(c.kwargs["source"] == "cron" for c in create_task.await_args_list))
        self.assertEqual(claim_loop.await_args_list, [
            call(101, "agent", "ar-reconciliation-01", created_by="scheduler"),
            call(102, "agent", "fleet-cmd-01", created_by="scheduler"),
        ])
        self.assertEqual(audit.await_args_list[0].kwargs["agent_name"], "ar-reconciliation-01")
        self.assertEqual(audit.await_args_list[0].kwargs["detail"]["claimed_by"], "ar-reconciliation-01")
        self.assertEqual(audit.await_args_list[1].kwargs["detail"]["claimed_by"], "fleet-cmd-01")

    async def test_explicit_global_orchestrator_is_used_and_cron_override_wins(self):
        docs = {
            "owner-01": {"triggers": {"cron": [{"cron": "* * * * *"}]}},
            "owner-02": {"triggers": {"cron": [
                {"cron": "* * * * *", "orchestrator": "fleet-cmd-01"},
            ]}},
        }

        _, _, claim_loop, _ = await self.run_sync(
            docs, default_orchestrator="global-commander")

        self.assertEqual(claim_loop.await_args_list, [
            call(101, "agent", "global-commander", created_by="scheduler"),
            call(102, "agent", "fleet-cmd-01", created_by="scheduler"),
        ])


if __name__ == "__main__":
    unittest.main()
