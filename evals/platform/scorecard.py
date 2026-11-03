"""Deterministic platform scorecards and bounded recommendation transitions.

Usage:
    PYTHONPATH=. python -m evals.platform.scorecard \
      --scenario evals/platform/core-v1/scenario.yaml \
      --input evals/platform/core-v1/examples/score-input.json \
      --output artifacts/platform-evals/core-v1/example/scorecard.json

This module never creates development Tasks and never invokes a model.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

SCHEMA_VERSION = 1
DIMENSIONS = {
    "task_goal": 20,
    "multi_agent": 20,
    "protocol": 15,
    "runtime": 10,
    "evidence_auth": 10,
    "playground": 15,
    "engineering_cost": 10,
}
REQUIRED_GATES = ("P0", "P1", "P2")
TRANSITIONS = {
    ("proposed", "accept"): "accepted",
    ("proposed", "reject"): "rejected",
    ("accepted", "implement"): "implemented",
    ("implemented", "verify"): "verified",
    ("implemented", "fail"): "failed",
    ("verified", "close"): "closed",
    ("failed", "close"): "closed",
}


class EvaluationError(ValueError):
    """Invalid scenario, measurement, or requested recommendation transition."""


def load_scenario(path: str | Path) -> dict[str, Any]:
    scenario_path = Path(path)
    scenario = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
    if not isinstance(scenario, dict):
        raise EvaluationError("Scenario must be a mapping")
    if scenario.get("schema_version") != SCHEMA_VERSION:
        raise EvaluationError(f"Unsupported scenario schema: {scenario.get('schema_version')}")
    if not scenario.get("id"):
        raise EvaluationError("Scenario id is required")
    dimensions = scenario.get("dimensions")
    if not isinstance(dimensions, dict) or set(dimensions) != set(DIMENSIONS):
        raise EvaluationError("Scenario dimensions do not match the platform rubric")
    for key, expected in DIMENSIONS.items():
        if dimensions[key].get("weight") != expected:
            raise EvaluationError(f"Unexpected weight for {key}")
    scenario["_path"] = str(scenario_path)
    return scenario


def _levels(measurement: dict[str, Any]) -> dict[str, int]:
    levels = measurement.get("dimensions")
    if not isinstance(levels, dict):
        raise EvaluationError("dimensions must be a mapping")
    if set(levels) != set(DIMENSIONS):
        raise EvaluationError("Measurement dimensions do not match the platform rubric")
    parsed = {}
    for key in DIMENSIONS:
        value = levels[key]
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 5:
            raise EvaluationError(f"{key} level must be an integer from 0 to 5")
        parsed[key] = value
    return parsed


def _rating(score: int, gates_passed: bool, baseline: bool, repeatability: dict[str, Any], scenario: dict[str, Any]) -> str:
    if not gates_passed:
        return "未评级"
    if score < 60:
        return "未达标"
    if score < 75 or not baseline:
        return "工程原型"
    reusable = scenario.get("repeatability", {})
    reusable_ready = (
        repeatability.get("runtime_types", 0) >= reusable.get("min_runtime_types", 2)
        and repeatability.get("third_party_agents", 0) >= reusable.get("min_third_party_agents", 2)
        and repeatability.get("consecutive_stable_runs", 0) >= reusable.get("min_consecutive_stable_runs", 3)
    )
    if score >= 90 and reusable_ready:
        return "可复用平台"
    return "平台 MVP 可用"


GUARDRAIL_FIELDS = (
    "unauthorized_effect_count",
    "secret_leak_count",
    "quality_regression",
    "cost_regression",
    "safety_regression",
)


def evaluate_north_star(
    measurement: dict[str, Any], score: int, rating: str, scenario: dict[str, Any],
) -> dict[str, Any]:
    """Release only on strict improvement of the scenario's declared metric."""
    baseline_comparable = bool((measurement.get("baseline") or {}).get("comparable"))
    base_ready = (
        baseline_comparable
        and rating in ("平台 MVP 可用", "可复用平台")
        and score >= scenario.get("stop_conditions", {}).get("target_score", 75)
    )
    data = measurement.get("north_star")
    required = (scenario.get("measurement_protocol") or {}).get("north_star")
    if data is None:
        return {
            "status": "unmeasured" if required else "not_applicable",
            "release_allowed": False if required else base_ready,
            "reason": "north_star_measurement_required" if required else None,
        }
    use_sz_vsr = required == "stratified_zero_human_verified_success_rate"
    candidate_key = "candidate_sz_vsr" if use_sz_vsr else "candidate_vgh"
    baseline_key = "baseline_sz_vsr" if use_sz_vsr else "baseline_vgh"
    candidate = data.get(candidate_key)
    baseline = data.get(baseline_key)
    guardrails = data.get("guardrails")
    if (
        not isinstance(candidate, (int, float)) or isinstance(candidate, bool)
        or not isinstance(baseline, (int, float)) or isinstance(baseline, bool)
        or not isinstance(guardrails, dict)
        or any(field not in guardrails for field in GUARDRAIL_FIELDS)
    ):
        return {
            "status": "unmeasured",
            "release_allowed": False,
            "reason": "north_star_measurement_or_guardrails_missing",
            "metric": required,
            candidate_key: candidate,
            baseline_key: baseline,
        }
    regression = (
        guardrails["unauthorized_effect_count"] != 0
        or guardrails["secret_leak_count"] != 0
        or any(guardrails[field] is not False for field in ("quality_regression", "cost_regression", "safety_regression"))
    )
    delta = candidate - baseline
    status = "guardrail_regression" if regression else ("improved" if delta > 0 else "not_improved")
    return {
        "status": status,
        "release_allowed": base_ready and status == "improved",
        "metric": required,
        candidate_key: candidate,
        baseline_key: baseline,
        "delta": delta,
        "strict_improvement_required": True,
        "guardrails": guardrails,
        "diagnostics": {
            "candidate_vgh": data.get("candidate_vgh"),
            "baseline_vgh": data.get("baseline_vgh"),
            "human_intervention_count": data.get("human_intervention_count"),
        },
        "reason": None if status == "improved" else status,
    }


def stop_reasons(
    measurement: dict[str, Any], score: int, rating: str, scenario: dict[str, Any],
    north_star_gate: dict[str, Any] | None = None,
) -> list[str]:
    progress = measurement.get("progress") or {}
    limits = scenario.get("stop_conditions", {})
    reasons = []
    if progress.get("maintainer_stop"):
        reasons.append("maintainer_stop")
    gate = north_star_gate or evaluate_north_star(measurement, score, rating, scenario)
    if gate["release_allowed"]:
        reasons.append("target_reached")
    if progress.get("no_improvement_rounds", 0) >= limits.get("max_no_improvement_rounds", 2):
        reasons.append("no_improvement")
    if progress.get("repeated_failure"):
        reasons.append("repeated_failure")
    if progress.get("experiments", 0) >= limits.get("max_experiments", 6):
        reasons.append("experiment_budget_exhausted")
    if progress.get("tokens", 0) >= limits.get("max_tokens", 2_000_000):
        reasons.append("token_budget_exhausted")
    if progress.get("minutes", 0) >= limits.get("max_minutes", 120):
        reasons.append("time_budget_exhausted")
    if progress.get("no_new_hypothesis"):
        reasons.append("no_new_hypothesis")
    return reasons


def calculate_scorecard(measurement: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    levels = _levels(measurement)
    breakdown = {
        key: {
            "level": levels[key],
            "weight": DIMENSIONS[key],
            "score": DIMENSIONS[key] * levels[key] // 5,
        }
        for key in DIMENSIONS
    }
    raw_score = sum(item["score"] for item in breakdown.values())
    gates = measurement.get("gates") or {}
    gates_passed = all(gates.get(gate) is True for gate in REQUIRED_GATES)
    baseline = bool((measurement.get("baseline") or {}).get("comparable"))
    cap = scenario.get("baseline", {}).get("cap_when_missing", 74)
    score = raw_score if baseline else min(raw_score, cap)
    rating = _rating(score, gates_passed, baseline, measurement.get("repeatability") or {}, scenario)
    north_star_gate = evaluate_north_star(measurement, score, rating, scenario)
    reasons = stop_reasons(measurement, score, rating, scenario, north_star_gate)
    return {
        "schema_version": SCHEMA_VERSION,
        "scenario_id": scenario["id"],
        "scenario_version": measurement.get("scenario_version") or scenario["id"],
        "platform_version": measurement.get("platform_version", "unknown"),
        "score": score,
        "raw_score": raw_score,
        "baseline_capped": score != raw_score,
        "rating": rating,
        "hard_gates": {
            **{gate: bool(gates.get(gate)) for gate in REQUIRED_GATES},
            "P3": baseline,
        },
        "dimension_scores": breakdown,
        "north_star": north_star_gate,
        "release_allowed": north_star_gate["release_allowed"],
        "stop_reasons": reasons,
        "next_experiment_allowed": not reasons,
        "scorecard_only": True,
        "auto_development_allowed": False,
    }


def transition_recommendation(status: str, action: str) -> str:
    try:
        return TRANSITIONS[(status, action)]
    except KeyError as exc:
        raise EvaluationError(f"Forbidden recommendation transition: {status} + {action}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Calculate a deterministic AMAgentFleet platform scorecard")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    scenario = load_scenario(args.scenario)
    measurement = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = calculate_scorecard(measurement, scenario)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
