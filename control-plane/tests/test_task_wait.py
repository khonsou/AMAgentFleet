from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml
from fastapi import HTTPException

from app import main, pool as poolmod
from app.execution import enrich_execution

ROOT = Path(__file__).resolve().parents[2]


def detail(loops, *, task_status="queued", pool_status="open"):
    return {
        "task": {"id": 42, "status": task_status, "pool_status": pool_status},
        "loops": [enrich_execution(dict(loop)) for loop in loops],
        "comments": [],
        "children": [],
    }


def loop(loop_id=11, status="running", version=1, error=None):
    return {
        "id": loop_id, "task_id": 42, "status": status, "version": version,
        "error": error, "failure_kind": "provider" if error else None,
        "recovery_action": None, "next_retry_at": None,
    }


class WaitEvaluationTests(unittest.TestCase):
    def test_all_delivered_requires_every_selected_loop(self):
        snapshot = detail([loop(11, "delivered"), loop(12, "running")])
        status, _ = poolmod._evaluate_wait(
            snapshot, target="all_delivered", loop_ids={11, 12}, version_gt=None)
        self.assertIsNone(status)
        snapshot = detail([loop(11, "delivered"), loop(12, "delivered")])
        status, _ = poolmod._evaluate_wait(
            snapshot, target="all_delivered", loop_ids={11, 12}, version_gt=None)
        self.assertEqual(status, "condition_met")

    def test_version_gt_requires_new_version_delivered(self):
        for state in ("returned", "running", "claimed"):
            with self.subTest(state=state):
                snapshot = detail([loop(11, state, version=2)])
                status, _ = poolmod._evaluate_wait(
                    snapshot, target="version_gt", loop_ids={11}, version_gt=1)
                self.assertIsNone(status)
        snapshot = detail([loop(11, "delivered", version=2)])
        status, reason = poolmod._evaluate_wait(
            snapshot, target="version_gt", loop_ids={11}, version_gt=1)
        self.assertEqual(status, "condition_met")
        self.assertIn("version=2", reason)

    def test_permanent_failure_is_not_terminal_success(self):
        snapshot = detail([loop(11, "claimed", error="provider exhausted")])
        for target in ("delivered", "terminal"):
            with self.subTest(target=target):
                status, reason = poolmod._evaluate_wait(
                    snapshot, target=target, loop_ids={11}, version_gt=None)
                self.assertEqual(status, "failed")
                self.assertIn("provider exhausted", reason)

    def test_closed_task_satisfies_terminal_wait(self):
        snapshot = detail([loop(11, "archived")], task_status="rejected", pool_status="done")
        status, _ = poolmod._evaluate_wait(
            snapshot, target="terminal", loop_ids={11}, version_gt=None)
        self.assertEqual(status, "condition_met")

    def test_missing_selected_loop_is_explicit_404(self):
        with self.assertRaises(poolmod.PoolError) as caught:
            poolmod._evaluate_wait(
                detail([loop(11)]), target="delivered", loop_ids={99}, version_gt=None)
        self.assertEqual(caught.exception.status, 404)


class WaitExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_reads_again_until_condition_changes(self):
        snapshots = [
            detail([loop(11, "running")]),
            detail([loop(11, "delivered")]),
        ]
        with patch("app.pool.task_detail", new=AsyncMock(side_effect=snapshots)) as read, \
                patch("app.pool.asyncio.sleep", new=AsyncMock()) as sleep:
            result = await poolmod.wait_task(
                42, target="delivered", loop_ids={11}, timeout_seconds=2)
        self.assertEqual(result["wait_status"], "condition_met")
        self.assertEqual(read.await_count, 2)
        sleep.assert_awaited_once()

    async def test_timeout_returns_last_snapshot_not_success(self):
        snapshot = detail([loop(11, "running")])
        with patch("app.pool.task_detail", new=AsyncMock(return_value=snapshot)):
            result = await poolmod.wait_task(
                42, target="delivered", loop_ids={11}, timeout_seconds=0)
        self.assertEqual(result["wait_status"], "timeout")
        self.assertEqual(result["loops"][0]["status"], "running")

    async def test_invalid_wait_arguments_are_rejected(self):
        for kwargs in (
            {"target": "unknown"},
            {"target": "version_gt"},
            {"target": "delivered", "loop_ids": set()},
            {"target": "delivered", "timeout_seconds": 121},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(poolmod.PoolError) as caught:
                await poolmod.wait_task(42, **kwargs)
            self.assertEqual(caught.exception.status, 422)

    async def test_endpoint_parses_loop_ids_and_attaches_outputs(self):
        snapshot = {"wait_status": "condition_met", "reason": "ok", **detail([loop(11, "delivered")])}
        with patch("app.main.poolmod.wait_task", new=AsyncMock(return_value=snapshot)) as wait, \
                patch("app.main._attach_latest_outputs", new=AsyncMock()) as attach:
            result = await main.task_wait(
                42, target="delivered", loop_ids="11, 11", version_gt=None, timeout_seconds=5)
        self.assertEqual(result["wait_status"], "condition_met")
        self.assertEqual(wait.await_args.kwargs["loop_ids"], {11})
        attach.assert_awaited_once_with(snapshot)
        with self.assertRaises(HTTPException) as caught:
            await main.task_wait(
                42, target="delivered", loop_ids="not-a-number", version_gt=None, timeout_seconds=5)
        self.assertEqual(caught.exception.status_code, 422)


class WaitProtocolTests(unittest.TestCase):
    def test_protocol_and_ops_rules_expose_bounded_wait(self):
        protocol = json.loads((ROOT / "fleet-ops-protocol.json").read_text(encoding="utf-8"))
        operation = next(item for item in protocol["operations"] if item["id"] == "tasks.wait")
        self.assertEqual(operation["method"], "GET")
        self.assertEqual(operation["path"], "/api/tasks/{task_id}/wait")
        self.assertNotIn("write", operation)
        self.assertEqual(operation["timeout_seconds"], 125)
        self.assertIn("version_gt", operation["query"]["target"])
        with (ROOT / "control-plane/agents/fleet-ops-01.yaml").open(encoding="utf-8") as handle:
            soul = yaml.safe_load(handle)["spec"]["agent"]["soul"]
        self.assertIn("固定使用 tasks.wait", soul)
        self.assertIn("不得在当前 Run 内反复调用 tasks.get", soul)
        self.assertIn("只有 wait_status=condition_met", soul)
        self.assertIn("全部放在 query，不放 body", soul)
        self.assertIn("loop_ids 是逗号分隔字符串", soul)
        self.assertIn('path_params={"task_id":<真实 Task ID>}', soul)
        self.assertIn("timeout_seconds 只能在 0–120 之间", soul)
        self.assertIn('path_params={"task_id":<真实 Task ID>}', soul)
        self.assertIn('body={"loop_id":<被采纳 Loop ID>}', soul)
        self.assertIn("两者缺一不可", soul)
        self.assertIn("协议中不存在 loops.accept", soul)


if __name__ == "__main__":
    unittest.main()
