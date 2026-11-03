"""SZ-VSR run semantics and deterministic suite aggregation (ADR-0045)."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from evals.platform.execution import ExecutionError


SZ_VSR = "stratified_zero_human_verified_success_rate"


def human_intervention_count(result: dict[str, Any]) -> int | None:
    human = result.get("human")
    if isinstance(human, dict) and human.get("human_intervention_count") is not None:
        return int(human["human_intervention_count"])
    if result.get("human_intervention_count") is not None:
        return int(result["human_intervention_count"])
    return None


def zero_human_verified_success(result: dict[str, Any]) -> int | None:
    """Return None when the run cannot be judged under the frozen contract."""
    accepted = result.get("accepted_goal")
    interventions = human_intervention_count(result)
    if accepted not in {0, 1} or interventions is None:
        return None
    return int(
        accepted == 1
        and interventions == 0
        and result.get("status") == "succeeded"
        and result.get("production_write_count") == 0
    )


def load_suite(path: str | Path) -> dict[str, Any]:
    suite_path = Path(path)
    suite = yaml.safe_load(suite_path.read_text(encoding="utf-8"))
    if not isinstance(suite, dict) or suite.get("schema_version") != 1:
        raise ExecutionError("评测集合 schema_version 必须为 1")
    if suite.get("north_star") != SZ_VSR:
        raise ExecutionError("评测集合必须使用 SZ-VSR")
    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ExecutionError("评测集合至少需要一个 case")
    ids = [case.get("id") for case in cases]
    if len(set(ids)) != len(ids) or any(not isinstance(case_id, str) or not case_id for case_id in ids):
        raise ExecutionError("case id 必须非空且不重复")
    for case in cases:
        if case.get("split") not in {"optimization", "holdout"}:
            raise ExecutionError(f"{case['id']} split 必须为 optimization 或 holdout")
        if not isinstance(case.get("weight"), (int, float)) or case["weight"] <= 0:
            raise ExecutionError(f"{case['id']} weight 必须为正数")
        if not case.get("scenario") or not case.get("difficulty") or not case.get("category"):
            raise ExecutionError(f"{case['id']} 缺少 scenario、difficulty 或 category")
        required_modes = case.get("required_modes") or ["single", "multi"]
        if (
            not isinstance(required_modes, list) or not required_modes
            or len(set(required_modes)) != len(required_modes)
            or any(mode not in {"single", "multi"} for mode in required_modes)
        ):
            raise ExecutionError(f"{case['id']} required_modes 无效")
        candidate_mode = case.get("candidate_mode") or "multi"
        if candidate_mode not in required_modes:
            raise ExecutionError(f"{case['id']} candidate_mode 必须包含在 required_modes 中")
        if case.get("comparison_group") and "single" not in required_modes:
            raise ExecutionError(f"{case['id']} 对照 case 必须包含 single 基线")
        case["required_modes"] = list(required_modes)
        case["candidate_mode"] = candidate_mode
    if not any(case["split"] == "holdout" for case in cases):
        raise ExecutionError("评测集合至少需要一个 Holdout case")
    suite["_path"] = str(suite_path.resolve())
    return suite


def _rate(runs: list[dict[str, Any]]) -> float | None:
    return sum(runs) / len(runs) if runs else None


def _weighted_case_rate(cases: list[dict[str, Any]], mode: str) -> float | None:
    eligible = [case for case in cases if case["modes"][mode]["valid_runs"] > 0]
    if not eligible:
        return None
    total_weight = sum(float(case["weight"]) for case in eligible)
    return sum(
        float(case["weight"]) * float(case["modes"][mode]["sz_vsr"])
        for case in eligible
    ) / total_weight


def _weighted_candidate_rate(cases: list[dict[str, Any]]) -> float | None:
    eligible = [case for case in cases if case["modes"][case["candidate_mode"]]["valid_runs"] > 0]
    if not eligible:
        return None
    total_weight = sum(float(case["weight"]) for case in eligible)
    return sum(
        float(case["weight"]) * float(case["modes"][case["candidate_mode"]]["sz_vsr"])
        for case in eligible
    ) / total_weight


def summarize_suite(results: list[dict[str, Any]], suite: dict[str, Any]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    invalid = []
    ignored = []
    definitions = {case["id"]: case for case in suite["cases"]}
    for result in results:
        if result.get("north_star_contract") != SZ_VSR:
            invalid.append({"run_id": result.get("run_id"), "reason": "legacy_metric_contract"})
            continue
        success = zero_human_verified_success(result)
        if success is None:
            invalid.append({"run_id": result.get("run_id"), "reason": "zero_human_measurement_missing"})
            continue
        mode = result.get("mode")
        definition = definitions.get(result.get("scenario_id"))
        if mode not in {"single", "multi"}:
            invalid.append({"run_id": result.get("run_id"), "reason": "unknown_mode"})
            continue
        if definition is None or mode not in definition["required_modes"]:
            ignored.append({"run_id": result.get("run_id"), "reason": "mode_not_required"})
            continue
        grouped[(result.get("scenario_id"), mode)].append(result)

    repeat_k = int(suite.get("repeat_k") or 3)
    if repeat_k <= 0:
        raise ExecutionError("repeat_k 必须为正整数")
    case_rows = []
    safety_violations = []
    for definition in suite["cases"]:
        case_id = definition["id"]
        mode_rows = {}
        for mode in ("single", "multi"):
            runs = sorted(
                grouped.get((case_id, mode), []),
                key=lambda item: (item.get("finished_at") or "", item.get("run_id") or ""),
            )
            successes = [zero_human_verified_success(item) for item in runs]
            latest = successes[-repeat_k:]
            tokens = [int(item.get("total_tokens") or 0) for item in runs]
            for item in runs:
                if int(item.get("production_write_count") or 0) != 0:
                    safety_violations.append(item.get("run_id"))
            mode_rows[mode] = {
                "valid_runs": len(runs),
                "zero_human_verified_successes": sum(successes),
                "sz_vsr": _rate(successes),
                "consecutive_zero_human_pass_k": len(latest) == repeat_k and all(latest),
                "run_ids": [item.get("run_id") for item in runs],
                "mean_tokens": (sum(tokens) / len(tokens)) if tokens else None,
            }
        single_rate = mode_rows["single"]["sz_vsr"]
        candidate_mode = definition["candidate_mode"]
        candidate_rate = mode_rows[candidate_mode]["sz_vsr"]
        comparable = bool(
            definition.get("comparison_group")
            and single_rate is not None and candidate_rate is not None
        )
        completed_e2e = sum(mode_rows[mode]["valid_runs"] > 0 for mode in definition["required_modes"])
        case_rows.append({
            **{key: definition[key] for key in (
                "id", "category", "difficulty", "split", "weight", "required_modes", "candidate_mode", "comparison_group",
            )},
            "modes": mode_rows,
            "comparable": comparable,
            "delta": (candidate_rate - single_rate) if comparable else None,
            "quality_regression": bool(comparable and candidate_rate < single_rate),
            "completed_e2e_runs": completed_e2e,
            "coverage_complete": completed_e2e == len(definition["required_modes"]),
        })

    comparable_rows = [case for case in case_rows if case["comparable"]]
    baseline = _weighted_case_rate(comparable_rows, "single")
    candidate = _weighted_candidate_rate(case_rows)
    comparison_candidate = _weighted_candidate_rate(comparable_rows)
    delta = comparison_candidate - baseline if baseline is not None and comparison_candidate is not None else None
    full_coverage = all(case["coverage_complete"] for case in case_rows)
    candidate_pass_k = all(
        case["modes"][case["candidate_mode"]]["consecutive_zero_human_pass_k"] for case in case_rows
    )
    holdout_rows = [case for case in case_rows if case["split"] == "holdout"]
    holdout_ready = all(
        case["coverage_complete"] and case["modes"][case["candidate_mode"]]["consecutive_zero_human_pass_k"]
        for case in holdout_rows
    )
    case_regression = any(case["quality_regression"] for case in case_rows)
    blockers = []
    if not comparable_rows:
        blockers.append("no_comparable_baseline")
    if not full_coverage:
        blockers.append("case_coverage_incomplete")
    if not candidate_pass_k:
        blockers.append("candidate_pass_k_missing")
    if not holdout_ready:
        blockers.append("holdout_not_ready")
    if case_regression:
        blockers.append("case_quality_regression")
    if safety_violations:
        blockers.append("safety_regression")
    if delta is not None and delta <= 0:
        blockers.append("not_improved")

    strata = []
    for difficulty in sorted({case["difficulty"] for case in case_rows}):
        rows = [case for case in case_rows if case["difficulty"] == difficulty]
        comparable = [case for case in rows if case["comparable"]]
        strata.append({
            "difficulty": difficulty,
            "case_ids": [case["id"] for case in rows],
            "baseline_sz_vsr": _weighted_case_rate(comparable, "single"),
            "candidate_sz_vsr": _weighted_candidate_rate(rows),
        })
    status = "unmeasured" if delta is None else ("improved" if delta > 0 else "not_improved")
    if safety_violations or case_regression:
        status = "guardrail_regression"
    return {
        "schema_version": 1,
        "suite_id": suite["id"],
        "north_star": SZ_VSR,
        "status": status,
        "baseline_sz_vsr": baseline,
        "candidate_sz_vsr": candidate,
        "comparison_candidate_sz_vsr": comparison_candidate,
        "delta": delta,
        "strict_improvement_required": True,
        "repeat_k": repeat_k,
        "release_allowed": not blockers,
        "release_blockers": blockers,
        "coverage": {
            "expected_cases": len(case_rows),
            "expected_e2e_runs": sum(len(case["required_modes"]) for case in case_rows),
            "completed_e2e_runs": sum(case["completed_e2e_runs"] for case in case_rows),
            "comparable_cases": len(comparable_rows),
            "full_case_coverage": full_coverage,
            "candidate_pass_k": candidate_pass_k,
            "holdout_ready": holdout_ready,
        },
        "cases": case_rows,
        "strata": strata,
        "invalid_runs": invalid,
        "ignored_runs": ignored,
        "safety_violation_run_ids": safety_violations,
        "auto_development_allowed": False,
    }
