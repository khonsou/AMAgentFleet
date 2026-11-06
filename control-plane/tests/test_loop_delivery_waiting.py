from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app import dispatcher, pool as poolmod
from app.execution import enrich_execution
from app.runtime.base import RunResult, RuntimeServiceError


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
    def __init__(self):
        self.loop = {
            "id": 7, "task_id": 9, "claimant_type": "agent", "claimant": "worker-01",
            "status": "claimed", "thread_id": "4c6a2c52-16f2-4b0d-9882-4f826ea58f6d",
            "model_profile": None, "resume_comment": None, "recursion_limit": 80,
            "recovery_action": None, "last_checkpoint_id": None,
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
        raise AssertionError(query)


class DispatcherDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def run_loop(self, runtime, pending: bool):
        conn = RunConnection()
        with (
            patch("app.dispatcher.pool", AsyncMock(return_value=FakeAcquire(conn))),
            patch("app.dispatcher._chain", AsyncMock(return_value=[(9, "worker-01")])),
            patch("app.dispatcher.get_runtime", return_value=runtime),
            patch("app.pool.has_pending_children", AsyncMock(return_value=pending)),
            patch("app.pool.wait_loop_for_children", AsyncMock()) as wait,
            patch("app.pool.deliver_loop", AsyncMock()) as deliver,
            patch("app.dispatcher._fail_loop", AsyncMock()) as fail,
            patch("app.dispatcher.audit", AsyncMock()),
        ):
            await dispatcher._run_loop(7)
        return wait, deliver, fail

    async def test_unpresented_intermediate_run_waits_instead_of_failing(self):
        runtime = type("Runtime", (), {
            "execute_run": AsyncMock(side_effect=RuntimeServiceError(
                200, "Artifact delivery incomplete: no produced output artifact was presented")),
        })()
        wait, deliver, fail = await self.run_loop(runtime, pending=True)
        wait.assert_awaited_once_with(7, thread_id="4c6a2c52-16f2-4b0d-9882-4f826ea58f6d")
        deliver.assert_not_awaited()
        fail.assert_not_awaited()

    async def test_presented_intermediate_run_does_not_enter_review(self):
        runtime = type("Runtime", (), {
            "execute_run": AsyncMock(return_value=RunResult(
                text="draft", tokens=12,
                artifacts=["/mnt/user-data/outputs/draft.md"],
            )),
        })()
        wait, deliver, fail = await self.run_loop(runtime, pending=True)
        self.assertIn("draft.md", wait.await_args.args[1])
        deliver.assert_not_awaited()
        fail.assert_not_awaited()

    async def test_final_unpresented_run_remains_failed(self):
        runtime = type("Runtime", (), {
            "execute_run": AsyncMock(side_effect=RuntimeServiceError(
                200, "Artifact delivery incomplete: no produced output artifact was presented")),
        })()
        wait, deliver, fail = await self.run_loop(runtime, pending=False)
        wait.assert_not_awaited()
        deliver.assert_not_awaited()
        fail.assert_awaited_once()

    async def test_final_run_with_children_settled_delivers(self):
        runtime = type("Runtime", (), {
            "execute_run": AsyncMock(return_value=RunResult(
                text="final", tokens=12,
                artifacts=["/mnt/user-data/outputs/final.md"],
            )),
        })()
        wait, deliver, fail = await self.run_loop(runtime, pending=False)
        wait.assert_not_awaited()
        deliver.assert_awaited_once_with(7, "final\n\n📎 产物：\n- final.md: /api/console/threads/4c6a2c52-16f2-4b0d-9882-4f826ea58f6d/artifacts/mnt/user-data/outputs/final.md", tokens=12, thread_id="4c6a2c52-16f2-4b0d-9882-4f826ea58f6d")
        fail.assert_not_awaited()


class WaitingProjectionTests(unittest.IsolatedAsyncioTestCase):
    def test_waiting_loop_is_nonterminal_and_not_final_delivery(self):
        loop = enrich_execution({
            "id": 7, "status": "delivered", "version": 1,
            "waiting_for_children": True, "error": None,
        })
        self.assertEqual(loop["execution_status"], "queued")
        self.assertEqual(loop["worker_state"], "waiting_children")
        self.assertFalse(loop["is_terminal"])
        self.assertFalse(loop["retryable"])
        detail = {
            "task": {"id": 9, "status": "running", "pool_status": "open"},
            "loops": [loop],
            "children": [{"id": 10, "status": "running", "execution_status": "running"}],
        }
        status, _ = poolmod._evaluate_wait(detail, target="delivered", loop_ids=None, version_gt=None)
        self.assertIsNone(status)
        state = poolmod._orchestration_state(detail)
        self.assertFalse(state["root_delivered"])

    async def test_child_completion_resumes_waiting_parent(self):
        conn = AsyncMock()
        conn.fetchval.return_value = 8
        conn.fetch.return_value = [{
            "id": 7, "agent_name": "reviewer", "status": "success", "loop_id": 17,
            "deliverable": "reviewed", "error": None, "summary_chars": 500,
        }]
        conn.fetchrow.return_value = {"id": 27}
        acquire = type("Acquire", (), {
            "__aenter__": AsyncMock(return_value=conn),
            "__aexit__": AsyncMock(return_value=None),
        })()
        db_pool = AsyncMock()
        db_pool.return_value.acquire = lambda: acquire
        with patch("app.pool.pool", new=db_pool), patch("app.pool.return_loop", new=AsyncMock()) as resume:
            await poolmod._resume_parent_when_children_settled(7)
        resume.assert_awaited_once_with(27, unittest.mock.ANY, author="system:child-completion")
        self.assertIn("waiting_for_children IS NOT FALSE", conn.fetchrow.await_args.args[0])

    async def test_wait_marks_loop_without_opening_review(self):
        conn = AsyncMock()
        transaction = type("Transaction", (), {
            "__aenter__": AsyncMock(return_value=None),
            "__aexit__": AsyncMock(return_value=None),
        })()
        conn.transaction = lambda: transaction
        conn.fetchrow.return_value = {"task_id": 9}
        acquire = type("Acquire", (), {
            "__aenter__": AsyncMock(return_value=conn),
            "__aexit__": AsyncMock(return_value=None),
        })()
        db_pool = AsyncMock()
        db_pool.return_value.acquire = lambda: acquire
        with patch("app.pool.pool", new=db_pool), patch("app.pool.audit", new=AsyncMock()):
            await poolmod.wait_loop_for_children(7, "draft", thread_id="thread-1")
        loop_sql = conn.fetchrow.await_args.args[0]
        self.assertIn("waiting_for_children=true", loop_sql)
        task_sql = conn.execute.await_args.args[0]
        self.assertIn("status='running', pool_status='open'", task_sql)


if __name__ == "__main__":
    unittest.main()
