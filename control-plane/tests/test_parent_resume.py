from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app import pool as poolmod


class ParentResumeTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _pool(conn):
        acquire = type("Acquire", (), {
            "__aenter__": AsyncMock(return_value=conn),
            "__aexit__": AsyncMock(return_value=None),
        })()
        db_pool = AsyncMock()
        db_pool.return_value.acquire = lambda: acquire
        return db_pool

    @staticmethod
    def _children(statuses=("success", "success")):
        return [
            {
                "id": 319 + index,
                "agent_name": agent,
                "status": statuses[index],
                "loop_id": 339 + index,
                "deliverable": f"verified output {index}",
                "error": None,
            }
            for index, agent in enumerate(("collector", "analyzer"))
        ]

    async def test_waits_for_all_children_then_resumes_parent_once(self):
        conn = AsyncMock()
        conn.fetchval.return_value = 318
        conn.fetchrow.return_value = {"id": 338}
        with patch("app.pool.pool", new=self._pool(conn)), \
                patch("app.pool.return_loop", new=AsyncMock()) as resume:
            conn.fetch.return_value = self._children(("success", "running"))
            await poolmod._resume_parent_when_children_settled(319)
            resume.assert_not_awaited()
            conn.fetch.return_value = self._children()
            await poolmod._resume_parent_when_children_settled(319)
        resume.assert_awaited_once()
        comment = resume.await_args.args[1]
        self.assertIn("Task #319", comment)
        self.assertIn("Task #320", comment)
        self.assertIn("不得重复派发相同子任务", comment)

    async def test_running_or_accepted_parent_is_not_resumed(self):
        conn = AsyncMock()
        conn.fetchval.return_value = 318
        conn.fetch.return_value = self._children()
        conn.fetchrow.return_value = None
        with patch("app.pool.pool", new=self._pool(conn)), \
                patch("app.pool.return_loop", new=AsyncMock()) as resume:
            await poolmod._resume_parent_when_children_settled(319)
        resume.assert_not_awaited()

    async def test_competing_completion_does_not_duplicate_resume(self):
        conn = AsyncMock()
        conn.fetchval.return_value = 318
        conn.fetch.return_value = self._children()
        conn.fetchrow.return_value = {"id": 338}
        with patch("app.pool.pool", new=self._pool(conn)), \
                patch("app.pool.return_loop", new=AsyncMock(side_effect=poolmod.PoolError(409, "changed"))):
            await poolmod._resume_parent_when_children_settled(319)

    async def test_delivery_triggers_parent_check(self):
        conn = AsyncMock()
        transaction = type("Transaction", (), {
            "__aenter__": AsyncMock(return_value=None),
            "__aexit__": AsyncMock(return_value=None),
        })()
        conn.transaction = lambda: transaction
        conn.fetchrow.return_value = {"task_id": 319}
        with patch("app.pool.pool", new=self._pool(conn)), \
                patch("app.pool.audit", new=AsyncMock()), \
                patch("app.pool._resume_parent_when_children_settled", new=AsyncMock()) as check:
            await poolmod.deliver_loop(339, "child result")
        check.assert_awaited_once_with(319)


if __name__ == "__main__":
    unittest.main()
