"""ADR-0032：运行时观测面、协议归属与 Thread 身份回归。"""

import json
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app import runtime_api
from app.runtime import RunResult, RuntimeServiceError
from app.runtime.deerflow import DeerFlowAdapter

ROOT = Path(__file__).resolve().parents[2]


class FakeAcquire:
    def __init__(self, assistant_id):
        self.assistant_id = assistant_id

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def fetchval(self, *_args):
        return self.assistant_id


class FakePool:
    def __init__(self, assistant_id):
        self.assistant_id = assistant_id

    def acquire(self):
        return FakeAcquire(self.assistant_id)


class ThreadIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_blank_thread_keeps_explicit_agent(self):
        adapter = DeerFlowAdapter()
        adapter.execute_run = AsyncMock(return_value=RunResult(messages=[]))
        with patch("app.runtime.deerflow.db_pool", AsyncMock(return_value=FakePool(None))):
            await adapter.send_message("thread-1", "查询", "fleet-ops-01")
        request = adapter.execute_run.await_args.args[0]
        self.assertEqual(request.agent_name, "fleet-ops-01")

    async def test_conflicting_agent_is_rejected_before_run(self):
        adapter = DeerFlowAdapter()
        adapter.execute_run = AsyncMock()
        with patch("app.runtime.deerflow.db_pool", AsyncMock(return_value=FakePool("lead_agent"))):
            with self.assertRaises(RuntimeServiceError) as raised:
                await adapter.send_message("thread-1", "查询", "fleet-ops-01")
        self.assertEqual(raised.exception.status, 409)
        adapter.execute_run.assert_not_awaited()


class RuntimeApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_capabilities_are_discoverable(self):
        capabilities = SimpleNamespace(resume=False, stream=False)
        runtime = type("Runtime", (), {"name": "deepseek-thin", "capabilities": lambda self: capabilities})()
        with patch("app.runtime_api.get_runtime", return_value=runtime):
            result = await runtime_api.runtime_capabilities()
        self.assertEqual(result["runtime"], "deepseek-thin")
        self.assertFalse(result["capabilities"]["resume"])

    async def test_overview_returns_runtime_dto(self):
        runtime = type("Runtime", (), {"get_overview": AsyncMock(return_value={"threads": 3})})()
        with patch("app.runtime_api.get_runtime", return_value=runtime):
            self.assertEqual(await runtime_api.overview(), {"threads": 3})

    async def test_capability_error_is_explicit(self):
        runtime = type("Runtime", (), {
            "get_thread_state": AsyncMock(side_effect=RuntimeServiceError(
                409, "capability_not_supported: thin.thread_state", kind="capability")),
        })()
        with patch("app.runtime_api.get_runtime", return_value=runtime):
            with self.assertRaises(HTTPException) as raised:
                await runtime_api.thread_state("thread-1")
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("capability_not_supported", raised.exception.detail)

    async def test_run_wait_translates_stable_payload(self):
        runtime = type("Runtime", (), {
            "execute_run": AsyncMock(return_value=RunResult(messages=[{"type": "ai", "content": "done"}], tokens=5)),
        })()
        body = {
            "assistant_id": "worker-01",
            "input": {"messages": [{"role": "user", "content": "任务"}]},
            "context": {"model_name": "reasoner"},
            "metadata": {"fleet_loop_id": 7, "fleet_recovery": "initial"},
            "config": {"recursion_limit": 300, "configurable": {"thread_id": "thread-1", "subagent_enabled": True}},
        }
        with patch("app.runtime_api.get_runtime", return_value=runtime):
            result = await runtime_api.run_wait(body)
        request = runtime.execute_run.await_args.args[0]
        self.assertEqual((request.agent_name, request.thread_id, request.loop_id), ("worker-01", "thread-1", 7))
        self.assertEqual(request.model_profile, "reasoner")
        self.assertEqual(result["messages"][0]["content"], "done")


class ObservationBoundaryTests(unittest.TestCase):
    def test_protocol_runtime_operations_use_control_plane(self):
        protocol = json.loads((ROOT / "fleet-ops-protocol.json").read_text(encoding="utf-8"))
        operations = {item["id"]: item for item in protocol["operations"]}
        for operation_id in (
            "overview.get", "fleet.threads", "threads.get", "threads.messages",
            "threads.create", "threads.send", "threads.destroy", "threads.artifact",
            "threads.state", "threads.token_usage", "runs.wait", "runs.get",
            "runs.messages", "runs.events", "runs.cancel", "runs.feedback",
        ):
            with self.subTest(operation=operation_id):
                self.assertEqual(operations[operation_id]["surface"], "control-plane")
        self.assertEqual(operations["runs.stream"]["surface"], "gateway")
        self.assertEqual(operations["threads.review_context"]["surface"], "console")

    def test_console_has_no_deerflow_sql_or_thread_state_logic(self):
        source = (ROOT / "console" / "backend" / "main.py").read_text(encoding="utf-8")
        for marker in ("threads_meta", "SELECT assistant_id", "/api/runs/wait"):
            self.assertNotIn(marker, source)


if __name__ == "__main__":
    unittest.main()
