from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml

from app import capabilities
from app.capability_plane import CapabilityService, MemoryStore
from app.main import app

ROOT = Path(__file__).resolve().parents[2]


class CapabilityApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = CapabilityService(MemoryStore(), allow_mock=True)
        self.request = type("Request", (), {
            "headers": {},
            "state": type("State", (), {"fap_actor": "offline-test"})(),
        })()

    async def test_workspace_lifecycle_through_api_functions(self):
        manifest = {
            "provider_id": "observer",
            "provider_type": "mock",
            "capabilities": [{
                "operation_id": "observe",
                "input_schema": {"type": "object"},
                "output_schema": {},
                "output_artifact": "Observation",
            }],
            "config": {"mode": "static", "results": {"observe": {"value": 1}}},
        }
        with patch("app.capabilities.service", new=self.service), \
                patch("app.capabilities.audit", new=AsyncMock()):
            await capabilities.register_provider(manifest, self.request)
            await capabilities.verify_provider("observer", self.request)
            invoked = await capabilities.invoke_capability(
                capabilities.InvokeInput(capability_id="observer/observe", input={}),
                self.request,
            )
            self.assertEqual(invoked["output"], {"value": 1})
            workspace = await capabilities.create_workspace({"goal": "api hard case"}, self.request)
            await capabilities.save_plan(workspace["id"], capabilities.PlanInput(plan={
                "nodes": [{"id": "observe", "capability_id": "observer/observe", "input": {}}],
                "edges": [],
            }))
            result = await capabilities.execute_plan(workspace["id"], self.request)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["outputs"]["observe"], {"value": 1})
        self.assertEqual(result["workspace"]["context"]["evidence"][0]["artifact_type"], None)

    def test_openapi_and_protocol_expose_same_capability_surface(self):
        paths = app.openapi()["paths"]
        protocol = json.loads((ROOT / "fleet-ops-protocol.json").read_text(encoding="utf-8"))
        expected = {
            "providers.list": "/api/capability-providers",
            "providers.register": "/api/capability-providers",
            "providers.verify": "/api/capability-providers/{provider_id}/verify",
            "providers.card": "/api/capability-providers/{provider_id}/card",
            "capabilities.list": "/api/capabilities",
            "capabilities.resolve": "/api/capabilities/resolve",
            "capabilities.invoke": "/api/capabilities/invoke",
            "workspaces.create": "/api/workspaces",
            "workspaces.get": "/api/workspaces/{workspace_id}",
            "workspaces.evidence": "/api/workspaces/{workspace_id}/evidence",
            "workspaces.plan": "/api/workspaces/{workspace_id}/plan",
            "workspaces.execute": "/api/workspaces/{workspace_id}/execute",
        }
        for operation_id, path in expected.items():
            operation = next(item for item in protocol["operations"] if item["id"] == operation_id)
            self.assertEqual(operation["path"], path)
            self.assertIn(path, paths)

    def test_meta_agents_are_capability_maximal_by_default(self):
        for name in ("fleet-cmd-01", "fleet-ops-01"):
            doc = yaml.safe_load((ROOT / "control-plane/agents" / f"{name}.yaml").read_text(encoding="utf-8"))
            self.assertEqual(doc["capability_policy"], "inherit_all")
            self.assertTrue(doc["inherit_new_capabilities"])
            soul = doc["spec"]["agent"]["soul"]
            self.assertIn("capabilities.invoke", soul)
            self.assertIn("workspaces", soul)
        ops = yaml.safe_load((ROOT / "control-plane/agents/fleet-ops-01.yaml").read_text(encoding="utf-8"))
        self.assertNotIn("allowed_subagents", ops["spec"]["agent"])
        self.assertNotIn("tool_groups", ops["spec"]["agent"])
        self.assertNotIn("不派生 subagent", ops["spec"]["agent"]["soul"])
        self.assertNotIn("不调用通用 HTTP", ops["spec"]["agent"]["soul"])


if __name__ == "__main__":
    unittest.main()
