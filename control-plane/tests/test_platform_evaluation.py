from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "control-plane") not in sys.path:
    sys.path.insert(0, str(ROOT / "control-plane"))

from app import evaluation, registry  # noqa: E402
from evals.platform.scorecard import (  # noqa: E402
    EvaluationError,
    calculate_scorecard,
    load_scenario,
    stop_reasons,
    transition_recommendation,
)


class PlatformEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenario_dir = ROOT / "evals/platform/core-v1"
        cls.scenario = load_scenario(cls.scenario_dir / "scenario.yaml")
        cls.measurement = json.loads((cls.scenario_dir / "examples/score-input.json").read_text())

    def test_scenario_and_controlled_facts_are_deterministic(self):
        self.assertEqual(sum(item["weight"] for item in self.scenario["dimensions"].values()), 100)
        boards = json.loads((self.scenario_dir / "fixtures/timeline-boards.json").read_text())["boards"]
        expected = json.loads((self.scenario_dir / "expected/facts.json").read_text())
        self.assertEqual(len(boards), expected["board_count"])
        self.assertEqual(sum(board["cards"] for board in boards), expected["card_count"])
        for status, count in expected["status_counts"].items():
            self.assertEqual(sum(board["status"][status] for board in boards), count)

    def test_example_score_is_computed_without_a_model(self):
        result = calculate_scorecard(self.measurement, self.scenario)
        self.assertEqual(result["score"], 73)
        self.assertEqual(result["rating"], "工程原型")
        self.assertTrue(result["next_experiment_allowed"])
        self.assertFalse(result["auto_development_allowed"])

    def test_missing_required_gate_is_unrated(self):
        measurement = copy.deepcopy(self.measurement)
        measurement["gates"]["P0"] = False
        result = calculate_scorecard(measurement, self.scenario)
        self.assertEqual(result["rating"], "未评级")
        self.assertFalse(result["hard_gates"]["P0"])

    def test_missing_baseline_caps_score_at_74(self):
        measurement = copy.deepcopy(self.measurement)
        measurement["dimensions"] = {key: 5 for key in measurement["dimensions"]}
        measurement["baseline"]["comparable"] = False
        result = calculate_scorecard(measurement, self.scenario)
        self.assertEqual(result["raw_score"], 100)
        self.assertEqual(result["score"], 74)
        self.assertEqual(result["rating"], "工程原型")
        self.assertTrue(result["baseline_capped"])

    def test_reusable_rating_requires_repeatability(self):
        measurement = copy.deepcopy(self.measurement)
        measurement["dimensions"] = {key: 5 for key in measurement["dimensions"]}
        result = calculate_scorecard(measurement, self.scenario)
        self.assertEqual(result["rating"], "平台 MVP 可用")
        self.assertIn("target_reached", result["stop_reasons"])
        measurement["repeatability"] = {
            "runtime_types": 2,
            "third_party_agents": 2,
            "consecutive_stable_runs": 3,
        }
        result = calculate_scorecard(measurement, self.scenario)
        self.assertEqual(result["rating"], "可复用平台")
        self.assertTrue(result["release_allowed"])

    def test_north_star_requires_strict_improvement(self):
        measurement = copy.deepcopy(self.measurement)
        measurement["dimensions"] = {key: 5 for key in measurement["dimensions"]}
        measurement["north_star"]["candidate_sz_vsr"] = 0.0
        result = calculate_scorecard(measurement, self.scenario)
        self.assertEqual(result["north_star"]["status"], "not_improved")
        self.assertFalse(result["release_allowed"])
        self.assertNotIn("target_reached", result["stop_reasons"])
        measurement["north_star"]["candidate_sz_vsr"] = 1.0
        result = calculate_scorecard(measurement, self.scenario)
        self.assertEqual(result["north_star"]["status"], "improved")
        self.assertTrue(result["release_allowed"])

    def test_north_star_missing_or_guardrail_regression_blocks_release(self):
        measurement = copy.deepcopy(self.measurement)
        measurement["dimensions"] = {key: 5 for key in measurement["dimensions"]}
        del measurement["north_star"]
        result = calculate_scorecard(measurement, self.scenario)
        self.assertEqual(result["north_star"]["status"], "unmeasured")
        self.assertFalse(result["release_allowed"])
        measurement = copy.deepcopy(self.measurement)
        measurement["dimensions"] = {key: 5 for key in measurement["dimensions"]}
        measurement["north_star"]["guardrails"]["cost_regression"] = True
        result = calculate_scorecard(measurement, self.scenario)
        self.assertEqual(result["north_star"]["status"], "guardrail_regression")
        self.assertFalse(result["release_allowed"])

    def test_stop_conditions_cover_stagnation_budget_and_human_stop(self):
        measurement = copy.deepcopy(self.measurement)
        measurement["progress"].update({
            "no_improvement_rounds": 2,
            "experiments": 6,
            "maintainer_stop": True,
        })
        reasons = stop_reasons(measurement, 73, "工程原型", self.scenario)
        self.assertIn("no_improvement", reasons)
        self.assertIn("experiment_budget_exhausted", reasons)
        self.assertIn("maintainer_stop", reasons)

    def test_remaining_stop_conditions_are_all_explicit(self):
        cases = [
            ({"repeated_failure": True}, "repeated_failure"),
            ({"tokens": 2_000_000}, "token_budget_exhausted"),
            ({"minutes": 120}, "time_budget_exhausted"),
            ({"no_new_hypothesis": True}, "no_new_hypothesis"),
        ]
        for progress, expected in cases:
            with self.subTest(expected=expected):
                measurement = copy.deepcopy(self.measurement)
                measurement["progress"].update(progress)
                reasons = stop_reasons(measurement, 73, "工程原型", self.scenario)
                self.assertIn(expected, reasons)

    def test_recommendation_cannot_jump_from_proposed_to_implemented(self):
        with self.assertRaises(EvaluationError):
            transition_recommendation("proposed", "implement")
        self.assertEqual(transition_recommendation("proposed", "accept"), "accepted")
        self.assertEqual(transition_recommendation("accepted", "implement"), "implemented")
        self.assertEqual(transition_recommendation("implemented", "verify"), "verified")
        self.assertEqual(transition_recommendation("verified", "close"), "closed")

    def test_rejected_recommendation_has_no_development_path(self):
        self.assertEqual(transition_recommendation("proposed", "reject"), "rejected")
        with self.assertRaises(EvaluationError):
            transition_recommendation("rejected", "implement")

    def test_invalid_dimension_level_is_rejected(self):
        measurement = copy.deepcopy(self.measurement)
        measurement["dimensions"]["task_goal"] = 6
        with self.assertRaises(EvaluationError):
            calculate_scorecard(measurement, self.scenario)


class PlatformEvaluationApiTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _request():
        return type("Request", (), {"state": type("State", (), {"fap_actor": None})()})()

    @staticmethod
    def _pool(conn):
        transaction = type("Transaction", (), {
            "__aenter__": AsyncMock(return_value=None),
            "__aexit__": AsyncMock(return_value=None),
        })()
        conn.transaction = lambda: transaction
        acquire = type("Acquire", (), {
            "__aenter__": AsyncMock(return_value=conn),
            "__aexit__": AsyncMock(return_value=None),
        })()
        db_pool = AsyncMock()
        db_pool.return_value.acquire = lambda: acquire
        return db_pool

    async def test_report_api_persists_scorecard_and_proposed_recommendation(self):
        measurement = json.loads((ROOT / "evals/platform/core-v1/examples/score-input.json").read_text())
        body = evaluation.ReportInput(
            scenario_id="core-v1",
            measurement=measurement,
            recommendations=[evaluation.RecommendationInput(
                dimension="engineering_cost",
                summary="Reduce redundant retrieval",
                evidence={"task_ids": [318, 319, 320]},
            )],
        )
        conn = AsyncMock()
        conn.fetchrow.side_effect = [
            {"id": 1, "created_at": "now"},
            {"id": 2, "report_id": 1, "dimension": "engineering_cost", "status": "proposed", "evidence": "{}"},
        ]
        with patch("app.evaluation.pool", new=self._pool(conn)), \
                patch("app.evaluation.audit", new=AsyncMock()):
            result = await evaluation.create_report(body, self._request())
        self.assertEqual(result["report_id"], 1)
        self.assertEqual(result["scorecard"]["score"], 73)
        self.assertEqual(result["recommendations"][0]["status"], "proposed")
        insert_sql = conn.fetchrow.await_args_list[0].args[0]
        self.assertNotIn("measurement", insert_sql)

    async def test_transition_rejects_automatic_implementation(self):
        conn = AsyncMock()
        conn.fetchrow.return_value = {"id": 2, "status": "proposed"}
        body = evaluation.TransitionInput(action="implement")
        with patch("app.evaluation.pool", new=self._pool(conn)):
            with self.assertRaises(evaluation.HTTPException) as caught:
                await evaluation.transition_recommendation_endpoint(2, body, self._request())
        self.assertEqual(caught.exception.status_code, 409)

    async def test_scenario_rejects_path_traversal_and_eval_agent_is_least_privilege(self):
        with self.assertRaises(evaluation.HTTPException) as caught:
            evaluation._scenario("../secrets")
        self.assertEqual(caught.exception.status_code, 422)
        with self.assertRaises(registry.RegistryError):
            registry.assert_mutable(registry.EVAL_AGENT_NAME)
        with (ROOT / "control-plane/agents/fleet-eval-01.yaml").open(encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        agent = doc["spec"]["agent"]
        self.assertEqual(agent["tool_groups"], [])
        self.assertEqual(agent["mcp_plugins"], [])
        self.assertEqual(agent["allowed_subagents"], [])
        self.assertFalse(agent["memory_enabled"])
        self.assertFalse(doc["triggers"]["agentCallable"])


if __name__ == "__main__":
    unittest.main()
