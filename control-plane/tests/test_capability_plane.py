from __future__ import annotations

import unittest

from app.capability_plane import (
    CapabilityError,
    CapabilityService,
    MemoryStore,
    validate_manifest,
    validate_plan,
)


def manifest(provider_id, operation="observe", *, mode="static", result=None, artifact="Observation", risk="read"):
    return {
        "provider_id": provider_id,
        "provider_type": "mock",
        "capabilities": [{
            "operation_id": operation,
            "input_schema": {"type": "object"},
            "output_schema": {},
            "input_artifacts": ["Goal"],
            "output_artifact": artifact,
            "risk": risk,
            "version": 1,
        }],
        "config": {"mode": mode, "results": {operation: result}},
    }


class CapabilityPlaneTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = CapabilityService(MemoryStore(), allow_mock=True)

    async def register(self, provider_id, operation="observe", **kwargs):
        await self.service.register_provider(manifest(provider_id, operation, **kwargs))
        return await self.service.verify_provider(provider_id)

    async def test_registration_defaults_to_capability_maximum(self):
        normalized = validate_manifest(manifest("provider-a"), allow_mock=True)
        self.assertEqual(normalized["capability_policy"], "inherit_all")
        self.assertTrue(normalized["inherit_new_capabilities"])
        with self.assertRaises(CapabilityError):
            validate_manifest(manifest("provider-a"), allow_mock=False)
        secret = manifest("provider-secret")
        secret["config"]["password"] = "must-not-pass"
        with self.assertRaises(CapabilityError):
            validate_manifest(secret, allow_mock=True)

    async def test_dynamic_discovery_resolve_invoke_and_awareness(self):
        await self.register("observer-a", result={"value": 2})
        resolved = await self.service.resolve({"output_artifact": "Observation", "risk_max": "read"})
        self.assertEqual(resolved["count"], 1)
        self.assertEqual(resolved["candidates"][0]["capability_id"], "observer-a/observe")
        result = await self.service.invoke("observer-a/observe", {"goal": "test"})
        self.assertEqual(result["output"], {"value": 2})
        card = await self.service.provider_card("observer-a")
        self.assertEqual(card["performance"]["invocations"], 1)
        self.assertEqual(card["performance"]["success_rate"], 1)
        self.assertTrue(card["health"]["verified"])

    async def test_schema_and_generic_risk_confirmation(self):
        await self.register("writer", operation="write_result", result={"written": True}, artifact="Effect", risk="write")
        with self.assertRaises(CapabilityError) as caught:
            await self.service.invoke("writer/write_result", {}, confirmed=False)
        self.assertEqual(caught.exception.code, "CONFIRMATION_REQUIRED")
        result = await self.service.invoke("writer/write_result", {}, confirmed=True)
        self.assertEqual(result["status"], "succeeded")
        with self.assertRaises(CapabilityError) as schema_error:
            await self.service.invoke("writer/write_result", [], confirmed=True)
        self.assertEqual(schema_error.exception.code, "SCHEMA_MISMATCH")

    async def test_mixed_dag_shares_evidence_and_completes_hard_case(self):
        await self.register("observer-a", result={"value": 2})
        await self.register("observer-b", result={"value": 3})
        await self.register("calculator", operation="calculate", mode="sum", artifact="Metric")
        await self.register("verifier", operation="verify", mode="verify", artifact="VerifiedFact")
        await self.register("reporter", operation="compose", result={"report": "complete"}, artifact="Report")
        workspace = await self.service.create_workspace({
            "goal": "mixed hard case",
            "constraints": ["offline only"],
            "stop_conditions": ["all nodes complete"],
        })
        await self.service.save_plan(workspace["id"], {
            "nodes": [
                {"id": "a", "capability_id": "observer-a/observe", "input": {}, "output_artifact": "Observation"},
                {"id": "b", "capability_id": "observer-b/observe", "input": {}, "output_artifact": "Observation"},
                {"id": "c", "capability_id": "calculator/calculate", "input": {"a": "node://a/output", "b": "node://b/output"}, "output_artifact": "Metric"},
                {"id": "d", "capability_id": "verifier/verify", "input": {"metric": "node://c/output"}, "output_artifact": "VerifiedFact", "acceptance": {"required_fields": ["verified"]}},
                {"id": "e", "capability_id": "reporter/compose", "input": {"verified": "node://d/output"}, "output_artifact": "Report", "acceptance": {"required_fields": ["report"]}},
            ],
            "edges": [
                {"from": "a", "to": "c"}, {"from": "b", "to": "c"},
                {"from": "c", "to": "d"}, {"from": "d", "to": "e"},
            ],
        })
        result = await self.service.execute_plan(workspace["id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["outputs"]["c"], {"total": 5})
        self.assertTrue(result["outputs"]["d"]["verified"])
        self.assertEqual(result["outputs"]["e"], {"report": "complete"})
        shared = result["workspace"]["context"]
        self.assertEqual(shared["status"] if "status" in shared else result["workspace"]["status"], "completed")
        self.assertEqual(len(shared["evidence"]), 5)
        self.assertEqual(
            [item["artifact_type"] for item in shared["evidence"]],
            ["Observation", "Observation", "Metric", "VerifiedFact", "Report"],
        )

    async def test_failed_provider_falls_back_and_records_awareness(self):
        await self.register("bad-observer", mode="fail")
        await self.register("good-observer", result={"value": 7})
        workspace = await self.service.create_workspace({"goal": "fallback"})
        await self.service.save_plan(workspace["id"], {
            "nodes": [{
                "id": "observe",
                "capability_id": "bad-observer/observe",
                "fallback_capability_ids": ["good-observer/observe"],
                "input": {},
            }],
            "edges": [],
        })
        result = await self.service.execute_plan(workspace["id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["outputs"]["observe"], {"value": 7})
        bad_card = await self.service.provider_card("bad-observer")
        self.assertEqual(bad_card["performance"]["invocations"], 1)
        self.assertEqual(bad_card["performance"]["success_rate"], 0)
        self.assertTrue(bad_card["performance"]["recent_errors"])

    async def test_failed_plan_requires_explicit_replan_then_resumes(self):
        await self.register("bad-observer", mode="fail")
        await self.register("good-observer", result={"value": 9})
        workspace = await self.service.create_workspace({"goal": "replan"})
        await self.service.save_plan(workspace["id"], {
            "nodes": [{"id": "observe", "capability_id": "bad-observer/observe", "input": {}, "on_failure": "replan"}],
            "edges": [],
        })
        failed = await self.service.execute_plan(workspace["id"])
        self.assertEqual(failed["status"], "needs_replan")
        await self.service.save_plan(workspace["id"], {
            "nodes": [{"id": "observe", "capability_id": "good-observer/observe", "input": {}}],
            "edges": [],
        })
        completed = await self.service.execute_plan(workspace["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["outputs"]["observe"], {"value": 9})

    async def test_plan_cycle_and_budget_are_hard_failures(self):
        with self.assertRaises(CapabilityError) as caught:
            validate_plan({
                "nodes": [
                    {"id": "a", "capability_id": "x/a"},
                    {"id": "b", "capability_id": "x/b"},
                ],
                "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
            })
        self.assertEqual(caught.exception.code, "PLAN_CYCLE")
        await self.register("observer-a", result={"value": 1})
        await self.register("observer-b", result={"value": 2})
        workspace = await self.service.create_workspace({"goal": "budget"})
        await self.service.save_plan(workspace["id"], {
            "nodes": [
                {"id": "a", "capability_id": "observer-a/observe", "input": {}},
                {"id": "b", "capability_id": "observer-b/observe", "input": {}},
            ],
            "edges": [],
            "max_invocations": 1,
        })
        with self.assertRaises(CapabilityError) as budget_error:
            await self.service.execute_plan(workspace["id"])
        self.assertEqual(budget_error.exception.code, "PLAN_BUDGET_EXCEEDED")

    async def test_manifest_change_requires_reverification(self):
        await self.register("observer-a", result={"value": 1})
        changed = manifest("observer-a", result={"value": 2})
        await self.service.register_provider(changed)
        card = await self.service.provider_card("observer-a")
        self.assertEqual(card["config_version"], 2)
        self.assertFalse(card["health"]["verified"])
        with self.assertRaises(CapabilityError) as caught:
            await self.service.invoke("observer-a/observe", {})
        self.assertEqual(caught.exception.code, "CAPABILITY_UNAVAILABLE")

    async def test_fallback_attempts_count_toward_invocation_budget(self):
        await self.register("bad-observer", mode="fail")
        await self.register("good-observer", result={"value": 1})
        workspace = await self.service.create_workspace({"goal": "fallback budget"})
        await self.service.save_plan(workspace["id"], {
            "nodes": [{
                "id": "observe",
                "capability_id": "bad-observer/observe",
                "fallback_capability_ids": ["good-observer/observe"],
                "input": {},
            }],
            "edges": [],
            "max_invocations": 1,
        })
        with self.assertRaises(CapabilityError) as caught:
            await self.service.execute_plan(workspace["id"])
        self.assertEqual(caught.exception.code, "PLAN_BUDGET_EXCEEDED")

    async def test_invalid_remote_config_is_explicit_not_fake_success(self):
        await self.service.register_provider(manifest("remote", mode="echo") | {"provider_type": "remote_agent"})
        with self.assertRaises(CapabilityError) as caught:
            await self.service.verify_provider("remote")
        self.assertEqual(caught.exception.code, "INVALID_REMOTE_URL")
        with self.assertRaises(CapabilityError) as unavailable:
            await self.service.invoke("remote/observe", {})
        self.assertEqual(unavailable.exception.code, "CAPABILITY_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
