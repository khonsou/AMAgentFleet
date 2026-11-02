from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.platform.core_runner import (  # noqa: E402
    DEFAULT_SCENARIO,
    run_core_evaluation,
    validate_baseline,
    validate_offline,
    validate_recommendations,
)
from evals.platform.execution import (  # noqa: E402
    BudgetExceeded,
    ExecutionBudget,
    ExecutionError,
    require_execution_projection,
    vgh_measurement,
)


def valid_delivery():
    return json.dumps({
        "fleet_pool_counts": {"open": 1, "review": 2, "done": 3},
        "timeline": {
            "board_count": 2,
            "card_count": 8,
            "status_counts": {"待执行": 4, "待发布": 2, "已发布": 2},
        },
        "sources": [
            {"operation_id": "eval.fixture.fleet_status", "captured_at": "2026-01-01T00:00:00Z"},
            {"operation_id": "eval.fixture.timeline_boards", "captured_at": "2026-01-01T00:00:00Z"},
        ],
        "summary": "两个受控来源已完成交叉核对。",
    }, ensure_ascii=False)


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
    """In-memory Fleet with one early parent delivery followed by auto-resume."""

    def __init__(self, mode):
        self.mode = mode
        self.phase = 0
        self.dispatches = []
        self.puts = []
        self.reports = []
        self.closed = []
        self.current_agents = {}

    def get_agent(self, name):
        return {"yaml": self.current_agents[name]} if name in self.current_agents else None

    def put_agent(self, name, doc, key):
        self.puts.append((name, key))
        self.current_agents[name] = doc
        return {"name": name}

    def dispatch(self, agent, prompt, model_profile, key, actor):
        self.dispatches.append((agent, key))
        return 1

    def task_detail(self, task_id):
        self.phase += 1
        children = [{"id": 2}, {"id": 3}] if self.mode == "multi" else []
        version = 2 if self.mode == "multi" and self.phase >= 2 else 1
        return {
            "task": {"id": 1, "status": "success", "pool_status": "review"},
            "loops": [{
                "id": 11,
                "task_id": 1,
                "thread_id": "thread-root",
                "status": "delivered",
                "version": version,
                "deliverable": valid_delivery(),
                **projection(),
            }],
            "comments": [],
            "children": children,
        }

    def tasks(self, task_ids):
        items = [{
            "id": 1,
            "agent_name": "fleet-cmd-01" if self.mode == "multi" else "fleet-eval-single-01",
            "latest_loop_id": 11,
            "latest_thread_id": "thread-root",
            "claimants": [],
            **projection(),
        }]
        if self.mode == "multi":
            children_terminal = self.phase >= 2
            for task_id, agent in ((2, "fleet-eval-source-a-01"), (3, "fleet-eval-source-b-01")):
                items.append({
                    "id": task_id,
                    "agent_name": agent,
                    "latest_loop_id": task_id + 10,
                    "latest_thread_id": f"thread-{task_id}",
                    "claimants": [],
                    **projection("succeeded" if children_terminal else "running", children_terminal),
                })
        return [item for item in items if item["id"] in task_ids]

    def token_usage(self, thread_id):
        return {
            "total_tokens": 100,
            "total_input_tokens": 60,
            "total_output_tokens": 40,
            "total_runs": 1,
        }

    def close_task(self, task_id, key, actor):
        self.closed.append(task_id)
        return {"pool_status": "done", "cancelled_runs": 1}

    def create_report(self, payload, key):
        self.reports.append((payload, key))
        return {"report_id": 7, "scorecard": {"score": 75}, "recommendations": payload["recommendations"]}


class InterpretClient(FakeClient):
    def dispatch(self, agent, prompt, model_profile, key, actor):
        self.dispatches.append((agent, key))
        return 99 if agent == "fleet-eval-01" else 1

    def task_detail(self, task_id):
        if task_id != 99:
            return super().task_detail(task_id)
        return {
            "task": {"id": 99},
            "loops": [{
                "id": 99,
                "task_id": 99,
                "thread_id": "thread-eval",
                "status": "delivered",
                "version": 1,
                "deliverable": json.dumps({"recommendations": [{
                    "dimension": "protocol", "summary": "补齐协议证据", "evidence": {},
                }]}),
                **projection(),
            }],
            "children": [],
        }

    def token_usage(self, thread_id):
        if thread_id == "thread-eval":
            return {
                "total_tokens": 2_000_000,
                "total_input_tokens": 1_000_000,
                "total_output_tokens": 1_000_000,
                "total_runs": 1,
            }
        return super().token_usage(thread_id)


class CoreEvaluationRunnerTests(unittest.TestCase):
    def test_offline_validation_has_no_network_or_real_tasks(self):
        result = validate_offline(DEFAULT_SCENARIO)
        self.assertEqual(result["status"], "passed")
        self.assertFalse(result["real_tasks_started"])
        self.assertEqual(set(result["agents_sha256"]), {
            "fleet-eval-source-a-01", "fleet-eval-source-b-01", "fleet-eval-single-01",
        })

    def test_single_and_multi_share_acceptance_and_build_comparable_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            single_client = FakeClient("single")
            single = run_core_evaluation(
                single_client, root / "baseline-single", "single",
                active_human_seconds=60,
                platform_version="test-version",
                sleep=lambda _: None,
            )
            self.assertEqual(single["accepted_goal"], 1)
            self.assertEqual(single["vgh"], 60)
            self.assertTrue(single["checks"]["no_child_tasks"])
            self.assertIsNone(single["report"])
            self.assertFalse(single["auto_development_allowed"])

            multi_client = FakeClient("multi")
            multi = run_core_evaluation(
                multi_client, root / "candidate-multi", "multi",
                active_human_seconds=30,
                independent_evidence={"P1": True, "P2": True, "playground_level": 3},
                baseline=single,
                persist=True,
                platform_version="test-version",
                sleep=lambda _: None,
            )
            self.assertEqual(multi["accepted_goal"], 1)
            self.assertEqual(multi["vgh"], 120)
            self.assertTrue(multi["checks"]["auto_resume_version_gte_2"])
            self.assertEqual(multi["baseline_comparison"]["vgh_delta"], 60)
            self.assertTrue(multi["measurement"]["baseline"]["comparable"])
            self.assertTrue(multi["measurement"]["gates"]["P0"])
            self.assertTrue(multi["measurement"]["gates"]["P1"])
            self.assertTrue(multi["measurement"]["gates"]["P2"])
            self.assertEqual(multi["report"]["report_id"], 7)
            self.assertEqual(len(multi_client.reports), 1)
            self.assertFalse(multi["auto_development_allowed"])
            self.assertTrue(all(
                item["evidence"]["run_id"] == "candidate-multi"
                for item in multi["recommendations"]
            ))

    def test_interpretation_tokens_cannot_push_run_past_hard_budget(self):
        client = InterpretClient("multi")
        with tempfile.TemporaryDirectory() as temporary:
            result = run_core_evaluation(
                client, Path(temporary) / "candidate-multi", "multi",
                active_human_seconds=60,
                interpret=True,
                platform_version="test-version",
                sleep=lambda _: None,
            )
        self.assertEqual(result["status"], "token_budget_exhausted")
        self.assertEqual(result["accepted_goal"], 0)
        self.assertEqual(result["vgh"], 0)
        self.assertIn("token_budget_exhausted", result["scorecard"]["stop_reasons"])

    def test_missing_human_time_never_produces_infinite_vgh(self):
        measured = vgh_measurement(1, 0)
        self.assertIsNone(measured["vgh"])
        self.assertEqual(measured["measurement_status"], "human_time_missing")
        self.assertEqual(vgh_measurement(0, 60)["vgh"], 0)

    def test_single_persist_is_rejected_before_any_real_task(self):
        client = FakeClient("single")
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ExecutionError):
                run_core_evaluation(
                    client, Path(temporary) / "single", "single", persist=True, sleep=lambda _: None)
        self.assertEqual(client.dispatches, [])
        self.assertEqual(client.puts, [])
        self.assertEqual(client.reports, [])

    def test_incompatible_baseline_is_rejected(self):
        baseline = {
            "run_id": "single-1",
            "mode": "single",
            "scenario_id": "core-v1",
            "prompt_version": "core-v1-runner-v1",
            "model_profile": "deepseek-default",
            "fixtures_sha256": "same",
            "accepted_goal": 1,
            "active_human_seconds": 60,
            "vgh": 60,
        }
        candidate = dict(baseline, run_id="multi-1", mode="multi")
        self.assertTrue(validate_baseline(baseline, candidate)["comparable"])
        with self.assertRaises(ExecutionError):
            validate_baseline(baseline, dict(candidate, fixtures_sha256="changed"))
        with self.assertRaises(ExecutionError):
            validate_baseline(dict(baseline, vgh=None), candidate)

    def test_structured_execution_fields_are_mandatory(self):
        with self.assertRaises(ExecutionError):
            require_execution_projection({"status": "claimed", "error": "provider"})
        result = require_execution_projection(projection("queued", False) | {"retryable": True})
        self.assertTrue(result["retryable"])

    def test_budget_uses_wall_and_token_hard_limits(self):
        now = [0.0]
        budget = ExecutionBudget(max_wall_seconds=10, max_tokens=100, clock=lambda: now[0])
        now[0] = 11
        with self.assertRaises(BudgetExceeded):
            budget.check()
        token_budget = ExecutionBudget(max_wall_seconds=100, max_tokens=100, clock=lambda: 0)
        token_budget.set_total_tokens(100)
        with self.assertRaises(BudgetExceeded):
            token_budget.check()

    def test_recommendations_are_schema_checked(self):
        valid = {"recommendations": [{"dimension": "protocol", "summary": "补齐结构化状态", "evidence": {}}]}
        self.assertEqual(validate_recommendations(json.dumps(valid))[0]["dimension"], "protocol")
        with self.assertRaises(ExecutionError):
            validate_recommendations({"recommendations": [{"dimension": "unknown", "summary": "x"}]})

    def test_agent_definitions_are_isolated(self):
        for name in ("fleet-eval-source-a-01", "fleet-eval-source-b-01", "fleet-eval-single-01"):
            doc = yaml.safe_load((ROOT / "control-plane/agents" / f"{name}.yaml").read_text(encoding="utf-8"))
            agent = doc["spec"]["agent"]
            self.assertFalse(agent["memory_enabled"])
            self.assertEqual(agent["skills"], [])
            self.assertEqual(agent["tool_groups"], [])
            self.assertEqual(agent["allowed_subagents"], [])
            self.assertEqual(doc["egress"], [])


if __name__ == "__main__":
    unittest.main()
