from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.platform.north_star import (  # noqa: E402
    load_suite,
    summarize_suite,
    zero_human_verified_success,
)
from evals.platform.suite_runner import (  # noqa: E402
    DEFAULT_SUITE,
    execute_case_run,
    load_case,
    validate_suite,
)


def projection(status="succeeded", terminal=True):
    return {
        "execution_status": status,
        "is_terminal": terminal,
        "retryable": False,
        "failure_kind": None,
        "last_error": None,
        "worker_state": "terminal" if terminal else "running",
        "next_retry_at": None,
    }


class FakeClient:
    def __init__(self, scenario, mode, output_dir, *, valid_time=True, invocations=1):
        self.scenario = scenario
        self.mode = mode
        self.output_dir = output_dir
        self.valid_time = valid_time
        self.invocations = invocations
        self.phase = 0
        self.current_agents = {}
        self.provider = None
        self.dispatches = []
        self.tokens = []
        self.checkpoint = datetime.now(timezone.utc)

    def get_agent(self, name):
        return {"yaml": self.current_agents[name]} if name in self.current_agents else None

    def put_agent(self, name, doc, key):
        self.current_agents[name] = doc
        return {"name": name}

    def register_provider(self, manifest, key):
        self.provider = manifest
        return {"provider_id": manifest["provider_id"]}

    def verify_provider(self, provider_id, key):
        return {"provider_id": provider_id, "health": {"verified": True}}

    def provider_card(self, provider_id):
        return {
            "provider_id": provider_id,
            "health": {"verified": True},
            "performance": {
                "invocations": self.invocations,
                "success_rate": 1 if self.invocations else None,
            },
        }

    def dispatch(self, agent, prompt, model_profile, key, actor):
        self.dispatches.append((agent, prompt))
        self.tokens = re.findall(r"ECHO-[AB]-[A-F0-9]+", prompt)
        return 1

    def _case_data(self):
        return json.loads((self.output_dir / "run-state.json").read_text(encoding="utf-8"))["case_data"]

    def _delivery(self):
        case_type = self.scenario["case_type"]
        if case_type == "time_now":
            captured = self.checkpoint if self.valid_time else datetime.fromtimestamp(
                self.checkpoint.timestamp() + 3600, timezone.utc)
            return json.dumps({
                "captured_at": captured.isoformat(),
                "local_time": captured.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
                "source": {"type": "fleet_checkpoint", "thread_id": "thread-root"},
            })
        if case_type == "external_info":
            data = self._case_data()["observation"]
            return json.dumps({
                "observation": data,
                "source": {
                    "capability_id": f"{self.provider['provider_id']}/read_external_observation",
                    "invocation_id": 1,
                },
            }, ensure_ascii=False)
        if case_type == "multi_echo":
            data = self._case_data()
            return json.dumps({
                "parts": [data["part_a"], data["part_b"]],
                "joined": f"{data['part_a']}|{data['part_b']}",
            })
        raise AssertionError(case_type)

    def task_detail(self, task_id):
        self.phase += 1
        multi = self.scenario["case_type"] == "multi_echo" and self.mode == "multi"
        version = 2 if multi and self.phase >= 2 else 1
        return {
            "task": {"id": 1, "status": "success"},
            "loops": [{
                "id": 11,
                "task_id": 1,
                "thread_id": "thread-root",
                "status": "delivered",
                "version": version,
                "deliverable": self._delivery(),
                **projection(),
            }],
            "comments": [{"author": "system:child-completion", "content": "children settled"}] if multi else [],
            "children": [{"id": 2}, {"id": 3}] if multi else [],
        }

    def tasks(self, task_ids):
        multi = self.scenario["case_type"] == "multi_echo" and self.mode == "multi"
        items = [{
            "id": 1,
            "agent_name": "fleet-cmd-01" if self.mode == "multi" else "fleet-eval-general-01",
            "latest_loop_id": 11,
            "latest_thread_id": "thread-root",
            **projection(),
        }]
        if multi:
            data = self._case_data()
            for task_id, agent, key in (
                (2, "fleet-eval-source-a-01", "part_a"),
                (3, "fleet-eval-source-b-01", "part_b"),
            ):
                items.append({
                    "id": task_id,
                    "agent_name": agent,
                    "latest_loop_id": task_id + 10,
                    "latest_thread_id": f"thread-{task_id}",
                    "latest_deliverable": data[key],
                    **projection("succeeded" if self.phase >= 2 else "running", self.phase >= 2),
                })
        return [item for item in items if item["id"] in task_ids]

    def token_usage(self, thread_id):
        return {"total_tokens": 100, "total_input_tokens": 60, "total_output_tokens": 40, "total_runs": 1}

    def thread_state(self, thread_id):
        return {"checkpoint": {"created_at": self.checkpoint.isoformat()}}

    def close_task(self, task_id, key, actor):
        return {"pool_status": "done", "cancelled_runs": 1}


def suite_run(case_id, mode, index, success, *, finished_at=None, production_write_count=0):
    return {
        "run_id": f"{case_id}-{mode}-{index}",
        "scenario_id": case_id,
        "north_star_contract": "stratified_zero_human_verified_success_rate",
        "mode": mode,
        "status": "succeeded",
        "accepted_goal": success,
        "production_write_count": production_write_count,
        "human": {"human_intervention_count": 0},
        "total_tokens": 100,
        "finished_at": finished_at or f"2026-11-02T00:00:{index:02d}+00:00",
    }


class SuiteRunnerTests(unittest.TestCase):
    def test_offline_validation_discovers_all_cases_without_side_effects(self):
        result = validate_suite(DEFAULT_SUITE)
        self.assertEqual(result["status"], "passed")
        self.assertEqual({case["id"] for case in result["cases"]}, {
            "core-v1", "time-now-v1", "multi-echo-v1", "external-info-v1",
        })
        self.assertEqual({(item["case"], item["mode"]) for item in result["required_e2e_runs"]}, {
            ("core-v1", "single"),
            ("core-v1", "multi"),
            ("time-now-v1", "single"),
            ("multi-echo-v1", "multi"),
            ("external-info-v1", "single"),
            ("external-info-v1", "multi"),
        })
        self.assertFalse(result["real_tasks_started"])
        self.assertFalse(result["external_services_started"])

    def test_time_case_requires_real_checkpoint_and_timezone_evidence(self):
        _, _, scenario = load_case(DEFAULT_SUITE, "time-now-v1")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "time-single"
            result = execute_case_run(
                FakeClient(scenario, "single", output), scenario, output, "single", sleep=lambda _: None)
        self.assertEqual(result["accepted_goal"], 1)
        self.assertEqual(result["zero_human_verified_success"], 1)
        self.assertTrue(result["checks"]["checkpoint_timestamp"])
        self.assertTrue(result["checks"]["timezone_conversion"])

    def test_time_case_rejects_answer_not_present_in_checkpoint(self):
        _, _, scenario = load_case(DEFAULT_SUITE, "time-now-v1")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "time-invalid"
            result = execute_case_run(
                FakeClient(scenario, "single", output, valid_time=False),
                scenario, output, "single", sleep=lambda _: None)
        # The fake emits a plausible clock reading but withholds it from Checkpoint evidence.
        self.assertFalse(result["checks"]["checkpoint_timestamp"])
        self.assertEqual(result["accepted_goal"], 0)
        self.assertEqual(result["zero_human_verified_success"], 0)

    def test_multi_echo_uses_real_children_and_system_resume(self):
        _, _, scenario = load_case(DEFAULT_SUITE, "multi-echo-v1")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "echo-multi"
            result = execute_case_run(
                FakeClient(scenario, "multi", output), scenario, output, "multi", sleep=lambda _: None)
        self.assertEqual(result["accepted_goal"], 1)
        self.assertTrue(result["checks"]["child_outputs_contain_tokens"])
        self.assertTrue(result["checks"]["auto_resume_version_gte_2"])
        self.assertEqual(result["max_root_loop_version"], 2)

    def test_external_case_uses_runtime_provider_without_leaking_answer(self):
        _, _, scenario = load_case(DEFAULT_SUITE, "external-info-v1")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "external-single"
            client = FakeClient(scenario, "single", output)
            result = execute_case_run(client, scenario, output, "single", sleep=lambda _: None)
            state = json.loads((output / "run-state.json").read_text(encoding="utf-8"))
        self.assertEqual(result["accepted_goal"], 1)
        self.assertTrue(result["checks"]["real_invocation"])
        self.assertEqual(client.provider["provider_type"], "remote_agent")
        self.assertNotIn(state["case_data"]["observation"]["value"], client.dispatches[0][1])
        self.assertNotIn("timeline", json.dumps(client.provider).lower())

    def test_external_case_rejects_missing_real_invocation(self):
        _, _, scenario = load_case(DEFAULT_SUITE, "external-info-v1")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "external-no-invocation"
            result = execute_case_run(
                FakeClient(scenario, "single", output, invocations=0),
                scenario, output, "single", sleep=lambda _: None)
        self.assertEqual(result["accepted_goal"], 0)
        self.assertEqual(result["zero_human_verified_success"], 0)
        self.assertFalse(result["checks"]["real_invocation"])

    def test_human_intervention_invalidates_otherwise_accepted_goal(self):
        result = suite_run("core-v1", "multi", 0, 1)
        self.assertEqual(zero_human_verified_success(result), 1)
        result["human"]["human_intervention_count"] = 1
        self.assertEqual(zero_human_verified_success(result), 0)
        del result["human"]
        self.assertIsNone(zero_human_verified_success(result))

    def test_suite_release_requires_coverage_pass_k_holdout_and_improvement(self):
        suite = load_suite(DEFAULT_SUITE)
        results = []
        for case in suite["cases"]:
            for mode in case["required_modes"]:
                success = 0 if case.get("comparison_group") and mode == "single" else 1
                results += [suite_run(case["id"], mode, index, success) for index in range(3)]
        summary = summarize_suite(results, suite)
        self.assertEqual(summary["baseline_sz_vsr"], 0)
        self.assertEqual(summary["candidate_sz_vsr"], 1)
        self.assertEqual(summary["coverage"]["expected_e2e_runs"], 6)
        self.assertEqual(summary["coverage"]["completed_e2e_runs"], 6)
        self.assertTrue(summary["coverage"]["holdout_ready"])
        self.assertTrue(summary["release_allowed"], summary["release_blockers"])

        no_improvement = summarize_suite([
            suite_run(case["id"], mode, index, 1)
            for case in suite["cases"] for mode in case["required_modes"] for index in range(3)
        ], suite)
        self.assertFalse(no_improvement["release_allowed"])
        self.assertIn("not_improved", no_improvement["release_blockers"])

    def test_non_required_mode_does_not_inflate_basic_suite(self):
        suite = load_suite(DEFAULT_SUITE)
        summary = summarize_suite([suite_run("time-now-v1", "multi", 0, 1)], suite)
        self.assertEqual(summary["coverage"]["completed_e2e_runs"], 0)
        self.assertEqual(summary["ignored_runs"], [{
            "run_id": "time-now-v1-multi-0", "reason": "mode_not_required",
        }])

    def test_suite_rejects_case_regression_and_safety_violation(self):
        suite = load_suite(DEFAULT_SUITE)
        results = [
            suite_run(case["id"], mode, index, 1)
            for case in suite["cases"] for mode in case["required_modes"] for index in range(3)
        ]
        results.append(suite_run("core-v1", "multi", 4, 0, production_write_count=1))
        summary = summarize_suite(results, suite)
        self.assertFalse(summary["release_allowed"])
        self.assertIn("case_quality_regression", summary["release_blockers"])
        self.assertIn("safety_regression", summary["release_blockers"])
        self.assertEqual(summary["status"], "guardrail_regression")


if __name__ == "__main__":
    unittest.main()
