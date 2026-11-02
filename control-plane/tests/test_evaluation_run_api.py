from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app import evaluation

ROOT = Path(__file__).resolve().parents[2]


class EvaluationRunApiTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def request():
        return type("Request", (), {
            "headers": {"Authorization": "Bearer dfp_testonly"},
            "state": type("State", (), {"fap_actor": "platform-maintainer"})(),
        })()

    async def test_manual_run_delegates_once_and_never_auto_develops(self):
        body = evaluation.EvaluationRunInput(mode="multi", run_id="offline-api-test", persist=False)
        executed = AsyncMock(return_value={
            "run_id": "offline-api-test-multi",
            "status": "succeeded",
            "accepted_goal": 1,
            "report": None,
        })
        with patch("app.evaluation._execute_evaluation_run", new=executed), \
                patch("app.evaluation.audit", new=AsyncMock()) as audit:
            result = await evaluation.create_evaluation_run(body, self.request())
        self.assertEqual(result["run_id"], "offline-api-test-multi")
        self.assertFalse(result["auto_development_allowed"])
        self.assertEqual(result["next_experiment_requires"], "maintainer_accept")
        self.assertEqual(executed.await_count, 1)
        self.assertEqual(executed.await_args.args[-1], "Bearer dfp_testonly")
        audit.assert_awaited_once()

    async def test_single_persist_is_rejected_before_execution(self):
        body = evaluation.EvaluationRunInput(mode="single", persist=True)
        executed = AsyncMock()
        with patch("app.evaluation._execute_evaluation_run", new=executed):
            with self.assertRaises(HTTPException) as caught:
                await evaluation.create_evaluation_run(body, self.request())
        self.assertEqual(caught.exception.status_code, 422)
        executed.assert_not_awaited()

    async def test_single_cannot_reference_candidate_baseline(self):
        body = evaluation.EvaluationRunInput(mode="single", baseline_run_id="candidate-multi")
        with self.assertRaises(HTTPException) as caught:
            await evaluation.create_evaluation_run(body, self.request())
        self.assertEqual(caught.exception.status_code, 422)

    async def test_artifact_path_traversal_is_rejected(self):
        body = evaluation.EvaluationRunInput(
            mode="multi", independent_evidence_path="../../secret.json")
        with self.assertRaises(HTTPException) as caught:
            await evaluation.create_evaluation_run(body, self.request())
        self.assertEqual(caught.exception.status_code, 422)

    def test_protocol_exposes_manual_human_only_run(self):
        protocol = json.loads((ROOT / "fleet-ops-protocol.json").read_text(encoding="utf-8"))
        operation = next(item for item in protocol["operations"] if item["id"] == "evals.run")
        self.assertEqual(operation["method"], "POST")
        self.assertEqual(operation["path"], "/api/eval/runs")
        self.assertEqual(operation["confirmation"], "human_only")
        self.assertIn("never starts follow-up development", operation["description"])


class RecommendationEvidenceChainTests(unittest.IsolatedAsyncioTestCase):
    async def test_transition_preserves_implementation_and_candidate_links(self):
        conn = AsyncMock()
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
        linked = {"implementation_commit": "abc123", "candidate_report_id": 9}
        conn.fetchrow.side_effect = [
            {"id": 2, "status": "accepted", "evidence": "{}"},
            {"id": 2, "status": "implemented", "evidence": json.dumps(linked)},
        ]
        body = evaluation.TransitionInput(
            action="implement", implementation_commit="abc123", candidate_report_id=9)
        with patch("app.evaluation.pool", new=db_pool), \
                patch("app.evaluation.audit", new=AsyncMock()):
            result = await evaluation.transition_recommendation_endpoint(
                2, body, EvaluationRunApiTests.request())
        self.assertEqual(result["evidence"], linked)
        update_call = conn.fetchrow.await_args_list[1]
        self.assertEqual(json.loads(update_call.args[3]), linked)

    async def test_verify_requires_candidate_and_north_star_release(self):
        with self.assertRaises(HTTPException) as missing:
            await evaluation._assert_candidate_release(
                AsyncMock(), evaluation.TransitionInput(action="verify"))
        self.assertEqual(missing.exception.status_code, 409)
        conn = AsyncMock()
        conn.fetchrow.return_value = {
            "id": 9,
            "scorecard": json.dumps({"north_star": {"release_allowed": False, "status": "not_improved"}}),
        }
        with self.assertRaises(HTTPException) as blocked:
            await evaluation._assert_candidate_release(
                conn, evaluation.TransitionInput(action="verify", candidate_report_id=9))
        self.assertEqual(blocked.exception.status_code, 409)
        conn.fetchrow.return_value = {
            "id": 9,
            "scorecard": json.dumps({"north_star": {"release_allowed": True, "status": "improved"}}),
        }
        await evaluation._assert_candidate_release(
            conn, evaluation.TransitionInput(action="verify", candidate_report_id=9))


if __name__ == "__main__":
    unittest.main()
