from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

import fleet_tools
from app import pool as poolmod
from evals.platform.numeric import NumericCheckError, percent, reconcile_ratio, require_conservation


class CollaborationBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_spawn_forwards_execution_and_summary_budgets(self):
        with patch("fleet_tools._cp", new=AsyncMock(return_value="{}")) as cp:
            await fleet_tools._spawn(
                "controlled task", assignee="worker", recursion_limit=80, summary_chars=300,
                config={"configurable": {"agent_name": "commander", "thread_id": "parent"}},
            )
        body = cp.await_args.args[1]
        self.assertEqual(body["recursion_limit"], 80)
        self.assertEqual(body["summary_chars"], 300)
        self.assertNotIn("password", body)

    async def test_claim_persists_per_loop_budget(self):
        conn = AsyncMock()
        conn.fetchval.side_effect = ["registered", 0]
        conn.fetchrow.side_effect = [
            {"id": 1, "pool_status": "open", "model_profile": None},
            None,
            {"id": 7, "recursion_limit": 80, "summary_chars": 300},
        ]
        with patch("app.models.resolve_profile", new=AsyncMock(return_value=(None, None, None))):
            row, created = await poolmod._claim_loop_with_conn(
                conn, 1, "agent", "worker", created_by="test",
                recursion_limit=80, summary_chars=300,
            )
        self.assertTrue(created)
        self.assertEqual(row["recursion_limit"], 80)
        self.assertEqual(row["summary_chars"], 300)
        insert_args = conn.fetchrow.await_args_list[-1].args
        self.assertEqual(insert_args[-2:], (80, 300))

    async def test_invalid_summary_budget_is_rejected(self):
        conn = AsyncMock()
        conn.fetchval.side_effect = ["registered", 0]
        conn.fetchrow.side_effect = [
            {"id": 1, "pool_status": "open", "model_profile": None},
            None,
        ]
        with patch("app.models.resolve_profile", new=AsyncMock(return_value=(None, None, None))):
            with self.assertRaises(poolmod.PoolError):
                await poolmod._claim_loop_with_conn(
                    conn, 1, "agent", "worker", created_by="test", summary_chars=20)


class NumericAndSummaryTests(unittest.TestCase):
    def test_conservation_catches_worker_arithmetic_error(self):
        self.assertEqual(require_conservation(359, [180, 177, 2], expected_total=359), 359)
        with self.assertRaises(NumericCheckError):
            require_conservation(359, [180, 177, 2], expected_total=511)

    def test_ratio_is_computed_deterministically(self):
        self.assertEqual(percent(359, 474), 75.74)
        self.assertEqual(percent(91, 94), 96.81)
        self.assertEqual(reconcile_ratio(359, 474, 75.74), 75.74)
        with self.assertRaises(NumericCheckError):
            reconcile_ratio(359, 474, 75.66)

    def test_summary_is_bounded_and_prefers_sentence_boundary(self):
        text = "第一句话。第二句话。第三句话。第四句话。"
        result = poolmod.bounded_summary(text, 18)
        self.assertLessEqual(len(result), 18)
        self.assertTrue(result.endswith("…"))
        self.assertIn("第一句话", result)


if __name__ == "__main__":
    unittest.main()
