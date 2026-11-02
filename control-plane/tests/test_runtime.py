"""ADR-0032：Runtime 选择与 DeerFlow 行为契约。"""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from app.runtime import RunRequest, RuntimeServiceError, get_runtime
from app.runtime.deerflow import DeerFlowAdapter
from app.runtime.resolver import reset_runtime_for_tests


class FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.body = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, _path, json):
        self.body = json
        return FakeResponse(self.payload)


class ResolverTests(unittest.TestCase):
    def tearDown(self):
        reset_runtime_for_tests()

    def test_default_runtime_is_deerflow(self):
        with patch.dict(os.environ, {}, clear=True):
            reset_runtime_for_tests()
            self.assertIsInstance(get_runtime(), DeerFlowAdapter)

    def test_unknown_runtime_fails_closed(self):
        with patch.dict(os.environ, {"FLEET_RUNTIME": "unknown"}, clear=True):
            reset_runtime_for_tests()
            with self.assertRaisesRegex(RuntimeError, "未知 FLEET_RUNTIME"):
                get_runtime()

    def test_execution_core_has_no_native_gateway_dependency(self):
        root = Path(__file__).resolve().parents[1] / "app"
        native_markers = ("GATEWAY", "/api/runs/wait", "/api/agents/import", "/api/scheduled-tasks")
        for filename in ("registry.py", "dispatcher.py", "recovery.py", "models.py", "main.py", "scheduler.py"):
            with self.subTest(file=filename):
                source = (root / filename).read_text(encoding="utf-8")
                for marker in native_markers:
                    self.assertNotIn(marker, source)


class DeerFlowContractTests(unittest.IsolatedAsyncioTestCase):
    def test_agent_package_rejects_other_formats(self):
        with self.assertRaises(RuntimeServiceError) as raised:
            DeerFlowAdapter().agent_package({"spec": {"format": "other", "version": 1}})
        self.assertEqual(raised.exception.status, 422)

    def test_agent_package_normalizes_missing_capability_fields_to_inherit_all(self):
        document = {
            "spec": {
                "format": "deerflow.custom-agent",
                "version": 1,
                "agent": {"name": "worker", "model": "deepseek-default"},
            }
        }
        package = DeerFlowAdapter().agent_package(document)
        for field in ("tool_groups", "mcp_plugins", "allowed_subagents", "skills"):
            self.assertIsNone(package["agent"][field])
        document["spec"]["agent"]["tool_groups"] = []
        package = DeerFlowAdapter().agent_package(document)
        self.assertEqual(package["agent"]["tool_groups"], [])

    async def test_run_body_and_result_match_legacy_semantics(self):
        client = FakeClient({
            "messages": [{
                "type": "ai", "content": "交付摘要",
                "response_metadata": {"token_usage": {"total_tokens": 42}},
            }],
            "artifacts": ["/mnt/user-data/outputs/report.md"],
        })
        request = RunRequest(
            agent_name="worker-01", thread_id="thread-1", loop_id=7,
            input_text="原始任务", model_profile="reasoner", recursion_limit=300,
        )
        result = await DeerFlowAdapter().execute_run(request, client_factory=lambda **_kwargs: client)
        self.assertEqual(client.body["assistant_id"], "worker-01")
        self.assertEqual(client.body["context"], {"model_name": "reasoner"})
        self.assertEqual(client.body["input"]["messages"][0]["content"], "原始任务")
        self.assertEqual(client.body["config"]["recursion_limit"], 300)
        self.assertEqual((result.text, result.tokens), ("交付摘要", 42))
        self.assertEqual(result.artifacts, ["/mnt/user-data/outputs/report.md"])

    async def test_resume_uses_opaque_checkpoint_and_steering(self):
        client = FakeClient({"messages": []})
        request = RunRequest(
            agent_name="worker-01", thread_id="thread-1", loop_id=7,
            input_text="原始任务", recovery_action="resume", checkpoint_ref="opaque-checkpoint",
        )
        await DeerFlowAdapter().execute_run(
            request, client_factory=lambda **_kwargs: client,
            recovery_instruction="【异常恢复指令】继续未完成步骤",
        )
        self.assertEqual(client.body["checkpoint_id"], "opaque-checkpoint")
        self.assertEqual(client.body["input"]["messages"][0]["content"], "【异常恢复指令】继续未完成步骤")
        self.assertNotEqual(client.body["input"]["messages"][0]["content"], "原始任务")


if __name__ == "__main__":
    unittest.main()
