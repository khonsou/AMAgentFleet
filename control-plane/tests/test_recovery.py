"""ADR-0029：Run 预算、spawn 幂等与 checkpoint 恢复请求回归。"""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import fleet_tools  # noqa: E402
from app import dispatcher  # noqa: E402
from app.recovery import classify_failure  # noqa: E402


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class FakeConnection:
    def __init__(self, rows):
        self.fetchrow = AsyncMock(side_effect=rows)
        self.execute = AsyncMock()

    def transaction(self):
        return FakeTransaction()


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return None


class FakeResponse:
    status_code = 200
    text = ""

    def json(self):
        return {"status": "success", "messages": []}


class FakeClient:
    bodies = []

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, _path, *, json):
        type(self).bodies.append(json)
        return FakeResponse()


def loop_row(**overrides):
    row = {
        "id": 21,
        "task_id": 11,
        "status": "claimed",
        "claimant_type": "agent",
        "claimant": "agent-1",
        "recovery_action": None,
        "continuation_count": 0,
        "resume_comment": None,
        "thread_id": "thread-1",
        "last_checkpoint_id": None,
    }
    row.update(overrides)
    return row


class RunBodyTests(unittest.IsolatedAsyncioTestCase):
    async def run_loop(self, loop):
        conn = FakeConnection([
            loop,
            {"id": 11, "input": "原始任务"},
            {"id": 11, "parent_task_id": None, "agent_name": "agent-1"},
        ])
        FakeClient.bodies = []
        with (
            patch("app.dispatcher.pool", AsyncMock(return_value=FakePool(conn))),
            patch("app.dispatcher.httpx.AsyncClient", FakeClient),
            patch("app.pool.deliver_loop", AsyncMock()),
            patch("app.dispatcher.audit", AsyncMock()),
        ):
            await dispatcher._run_loop(21)
        self.assertEqual(len(FakeClient.bodies), 1)
        return FakeClient.bodies[0]

    async def test_initial_run_has_explicit_budget_and_original_input(self):
        body = await self.run_loop(loop_row())
        self.assertEqual(body["config"]["recursion_limit"], 300)
        self.assertEqual(body["input"]["messages"][0]["content"], "原始任务")
        self.assertNotIn("checkpoint_id", body)

    async def test_resume_uses_checkpoint_with_steering_instead_of_original_input(self):
        body = await self.run_loop(loop_row(
            recovery_action="resume", last_checkpoint_id="checkpoint-9"))
        messages = body["input"]["messages"]
        self.assertEqual(len(messages), 1)
        self.assertNotEqual(messages[0]["content"], "原始任务")
        self.assertIn("异常恢复指令", messages[0]["content"])
        self.assertIn("不要重复任何已完成的读取或探测", messages[0]["content"])
        self.assertIn("停止调用工具，交付真实阻塞原因", messages[0]["content"])
        self.assertEqual(body["checkpoint_id"], "checkpoint-9")
        self.assertEqual(body["config"]["recursion_limit"], 300)
        self.assertEqual(body["metadata"]["fleet_recovery"], "resume")


class FailureClassificationTests(unittest.TestCase):
    def test_recursion_is_distinct_from_transient_and_permanent(self):
        self.assertEqual(classify_failure("Recursion limit of 100 reached"), "recursion")
        self.assertEqual(classify_failure("temporary", status_code=503), "transient")
        self.assertEqual(classify_failure("connect failed", transport=True), "transient")
        self.assertEqual(classify_failure("PAT scope denied", status_code=403), "permanent")
        self.assertEqual(classify_failure("bad request", status_code=422), "permanent")


class SpawnIdempotencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_parent_retry_is_stable_and_explicit_key_distinguishes_work(self):
        config = {"configurable": {"agent_name": "fleet-cmd-01", "thread_id": "parent-thread"}}
        calls = []

        async def capture(_path, body, *, idempotency_key=None):
            calls.append((body, idempotency_key))
            return '{"task_id":239}'

        with patch("fleet_tools._cp", new=capture):
            await fleet_tools._spawn("同文子任务", assignee="worker", config=config)
            await fleet_tools._spawn("同文子任务", assignee="worker", config=config)
            await fleet_tools._spawn("同文子任务", assignee="worker", spawn_key="second", config=config)

        self.assertEqual(calls[0][1], calls[1][1])
        self.assertNotEqual(calls[0][1], calls[2][1])
        self.assertTrue(calls[0][1].startswith("spawn:"))
        self.assertEqual(calls[0][0]["idempotency_key"], calls[0][1])
        self.assertEqual(calls[0][0]["caller_thread_id"], "parent-thread")


class ManualResumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_loop_fetches_checkpoint_and_enqueues_once(self):
        conn = FakeConnection([
            loop_row(last_checkpoint_id=None),
            {"id": 21},
        ])
        with (
            patch("app.dispatcher.pool", AsyncMock(return_value=FakePool(conn))),
            patch("app.dispatcher.latest_checkpoint", AsyncMock(return_value="checkpoint-latest")),
            patch("app.dispatcher.enqueue_loop_run", AsyncMock()) as enqueue,
            patch("app.dispatcher.audit", AsyncMock()),
        ):
            result = await dispatcher.resume_loop(21)
        self.assertEqual(result["checkpoint_id"], "checkpoint-latest")
        enqueue.assert_awaited_once_with(21)
        update = conn.fetchrow.await_args_list[1]
        self.assertIn("continuation_count=continuation_count+1", update.args[0])
        self.assertEqual(update.args[2], "checkpoint-latest")


if __name__ == "__main__":
    unittest.main()
