"""Platform evaluation report persistence and bounded recommendation API (ADR-0035)."""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from evals.platform.scorecard import (
    EvaluationError,
    calculate_scorecard,
    load_scenario,
    transition_recommendation,
)

from .db import audit, pool

ROOT = Path(__file__).resolve().parents[2]
EVAL_RUN_ROOT = ROOT / "artifacts/platform-evals/core-v1/runs"
router = APIRouter(tags=["platform-evaluation"])
SCENARIO_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class RecommendationInput(BaseModel):
    dimension: str = Field(min_length=1, max_length=64)
    summary: str = Field(min_length=1, max_length=2000)
    evidence: dict[str, Any] = Field(default_factory=dict)


class ReportInput(BaseModel):
    scenario_id: str = "core-v1"
    measurement: dict[str, Any]
    recommendations: list[RecommendationInput] = Field(default_factory=list, max_length=10)


class TransitionInput(BaseModel):
    action: str = Field(min_length=1, max_length=32)
    implementation_commit: str | None = Field(default=None, max_length=64)
    candidate_report_id: int | None = Field(default=None, ge=1)
    change_summary: str | None = Field(default=None, max_length=2000)


class EvaluationRunInput(BaseModel):
    scenario_id: str = "core-v1"
    mode: Literal["single", "multi"]
    run_id: str | None = Field(default=None, max_length=64)
    active_human_seconds: float = Field(default=0, ge=0)
    baseline_run_id: str | None = Field(default=None, max_length=64)
    independent_evidence_path: str | None = Field(default=None, max_length=256)
    persist: bool = False
    interpret: bool = True
    prepare: bool = True


def _scenario(scenario_id: str) -> dict[str, Any]:
    if not SCENARIO_ID.fullmatch(scenario_id):
        raise HTTPException(422, "Invalid scenario_id")
    path = ROOT / "evals" / "platform" / scenario_id / "scenario.yaml"
    try:
        return load_scenario(path)
    except (OSError, EvaluationError) as exc:
        raise HTTPException(422, str(exc)) from exc


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


JSON_FIELDS = {"hard_gates", "dimension_scores", "baseline", "stop_reasons", "scorecard", "evidence"}


def _report(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _json(value) if key in JSON_FIELDS else value
        for key, value in row.items()
    }


@router.post("/api/eval/reports", status_code=201)
async def create_report(body: ReportInput, request: Request) -> dict[str, Any]:
    scenario = _scenario(body.scenario_id)
    try:
        scorecard = calculate_scorecard(body.measurement, scenario)
    except EvaluationError as exc:
        raise HTTPException(422, str(exc)) from exc
    actor = getattr(request.state, "fap_actor", None) or "fleet-eval-01"
    async with (await pool()).acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """INSERT INTO fleet_eval_reports
                   (scenario_id, scenario_version, platform_version, score, raw_score,
                    rating, hard_gates, dimension_scores, baseline, stop_reasons, scorecard)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                   RETURNING id, created_at""",
                scorecard["scenario_id"], scorecard["scenario_version"], scorecard["platform_version"],
                scorecard["score"], scorecard["raw_score"], scorecard["rating"],
                json.dumps(scorecard["hard_gates"]), json.dumps(scorecard["dimension_scores"]),
                json.dumps(body.measurement.get("baseline") or {}),
                json.dumps(scorecard["stop_reasons"]), json.dumps(scorecard),
            )
            report_id = row["id"]
            recommendations = []
            for item in body.recommendations:
                rec = await conn.fetchrow(
                    """INSERT INTO fleet_eval_recommendations (report_id, dimension, summary, evidence)
                       VALUES ($1,$2,$3,$4) RETURNING *""",
                    report_id, item.dimension, item.summary, json.dumps(item.evidence),
                )
                recommendations.append(_report(dict(rec)))
    await audit(
        "eval.reported",
        agent_name="fleet-eval-01",
        actor=actor,
        detail={
            "report_id": report_id,
            "scenario_id": scorecard["scenario_id"],
            "platform_version": scorecard["platform_version"],
            "score": scorecard["score"],
            "rating": scorecard["rating"],
            "recommendation_count": len(recommendations),
        },
    )
    return {
        "report_id": report_id,
        "created_at": row["created_at"],
        "scorecard": scorecard,
        "recommendations": recommendations,
    }


@router.get("/api/eval/reports")
async def list_reports(
    scenario_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    async with (await pool()).acquire() as conn:
        if scenario_id:
            rows = await conn.fetch(
                """SELECT id, scenario_id, scenario_version, platform_version, score, raw_score,
                          rating, hard_gates, dimension_scores, baseline, stop_reasons, scorecard, created_at
                   FROM fleet_eval_reports WHERE scenario_id=$1
                   ORDER BY id DESC LIMIT $2 OFFSET $3""",
                scenario_id, limit, offset,
            )
        else:
            rows = await conn.fetch(
                """SELECT id, scenario_id, scenario_version, platform_version, score, raw_score,
                          rating, hard_gates, dimension_scores, baseline, stop_reasons, scorecard, created_at
                   FROM fleet_eval_reports ORDER BY id DESC LIMIT $1 OFFSET $2""",
                limit, offset,
            )
    return [_report(dict(row)) for row in rows]


@router.get("/api/eval/recommendations")
async def list_recommendations(
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    async with (await pool()).acquire() as conn:
        if status:
            rows = await conn.fetch(
                "SELECT * FROM fleet_eval_recommendations WHERE status=$1 ORDER BY id DESC LIMIT $2 OFFSET $3",
                status, limit, offset,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM fleet_eval_recommendations ORDER BY id DESC LIMIT $1 OFFSET $2",
                limit, offset,
            )
    return [_report(dict(row)) for row in rows]


async def _assert_candidate_release(conn, body: TransitionInput) -> None:
    if body.action != "verify":
        return
    if body.candidate_report_id is None:
        raise HTTPException(409, {
            "code": "CANDIDATE_REPORT_REQUIRED",
            "message": "verify 必须携带 candidate_report_id 并通过 SZ-VSR improve 验收门",
        })
    report = await conn.fetchrow(
        "SELECT id,scorecard FROM fleet_eval_reports WHERE id=$1", body.candidate_report_id)
    if report is None:
        raise HTTPException(404, f"candidate report {body.candidate_report_id} not found")
    scorecard = _json(report["scorecard"]) or {}
    gate = scorecard.get("north_star") or {}
    if not gate.get("release_allowed"):
        raise HTTPException(409, {
            "code": "NORTH_STAR_NOT_IMPROVED",
            "message": "候选报告未满足 SZ-VSR 严格改善、硬门、评分与护栏要求",
            "details": gate,
        })


@router.post("/api/eval/recommendations/{recommendation_id}/transition")
async def transition_recommendation_endpoint(recommendation_id: int, body: TransitionInput, request: Request) -> dict[str, Any]:
    actor = getattr(request.state, "fap_actor", None) or "human"
    async with (await pool()).acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM fleet_eval_recommendations WHERE id=$1 FOR UPDATE", recommendation_id)
            if row is None:
                raise HTTPException(404, "recommendation not found")
            try:
                status = transition_recommendation(row["status"], body.action)
            except EvaluationError as exc:
                raise HTTPException(409, str(exc)) from exc
            await _assert_candidate_release(conn, body)
            evidence = _json(row.get("evidence")) or {}
            links = {
                key: value for key, value in {
                    "implementation_commit": body.implementation_commit,
                    "candidate_report_id": body.candidate_report_id,
                    "change_summary": body.change_summary,
                }.items() if value is not None
            }
            evidence.update(links)
            updated = await conn.fetchrow(
                """UPDATE fleet_eval_recommendations
                   SET status=$2, evidence=$3, updated_at=now()
                   WHERE id=$1 RETURNING *""",
                recommendation_id, status, json.dumps(evidence),
            )
    await audit(
        "eval.recommendation_transition",
        agent_name="fleet-eval-01",
        actor=actor,
        detail={
            "recommendation_id": recommendation_id,
            "from": row["status"],
            "to": status,
            "implementation_commit": body.implementation_commit,
            "candidate_report_id": body.candidate_report_id,
        },
    )
    return _report(dict(updated))


def _evaluation_run_locations(body: EvaluationRunInput) -> tuple[Path, dict[str, Any] | None, dict[str, Any] | None]:
    from evals.platform.execution import ExecutionError, artifact_path, read_json

    if body.scenario_id != "core-v1":
        raise HTTPException(422, "当前手动启动器只支持 core-v1")
    run_id = body.run_id or f"core-{uuid.uuid4().hex[:12]}"
    for value in (run_id, body.baseline_run_id):
        if value is not None and not SCENARIO_ID.fullmatch(value):
            raise HTTPException(422, "run_id 只能包含小写字母、数字和连字符")
    try:
        output = artifact_path(EVAL_RUN_ROOT, f"{run_id}-{body.mode}")
        baseline = None
        if body.baseline_run_id:
            baseline = read_json(artifact_path(EVAL_RUN_ROOT, body.baseline_run_id) / "run.json")
        independent = None
        if body.independent_evidence_path:
            independent = read_json(artifact_path(EVAL_RUN_ROOT, body.independent_evidence_path))
    except (ExecutionError, OSError, ValueError) as exc:
        raise HTTPException(422, f"评测产物路径或内容无效：{exc}") from exc
    return output, baseline, independent


def _run_evaluation_sync(
    body: EvaluationRunInput,
    output: Path,
    baseline: dict[str, Any] | None,
    independent: dict[str, Any] | None,
    authorization: str,
) -> dict[str, Any]:
    from evals.platform.core_runner import DEFAULT_SCENARIO, run_core_evaluation
    from evals.platform.execution import FleetClient

    pat = authorization[7:].strip() if authorization.startswith("Bearer ") else None
    client = FleetClient(
        os.environ.get("FLEET_CONTROL_PLANE_SELF_URL")
        or os.environ.get("FLEET_API")
        or "http://127.0.0.1:8003",
        pat,
    )
    return run_core_evaluation(
        client,
        output,
        body.mode,
        scenario_path=DEFAULT_SCENARIO,
        active_human_seconds=body.active_human_seconds,
        independent_evidence=independent,
        baseline=baseline,
        persist=body.persist,
        interpret=body.interpret and body.mode == "multi",
        prepare=body.prepare,
    )


async def _execute_evaluation_run(
    body: EvaluationRunInput,
    output: Path,
    baseline: dict[str, Any] | None,
    independent: dict[str, Any] | None,
    authorization: str,
) -> dict[str, Any]:
    # 手动同步入口：真实模型执行发生在线程中，不阻塞 Control Plane 事件循环。
    return await asyncio.to_thread(
        _run_evaluation_sync, body, output, baseline, independent, authorization)


@router.post("/api/eval/runs", status_code=201)
async def create_evaluation_run(body: EvaluationRunInput, request: Request) -> dict[str, Any]:
    """维护者显式触发一轮；绝不自动接受建议或启动下一轮开发。"""
    if body.persist and body.mode != "multi":
        raise HTTPException(422, "single 技术对照不得持久化为平台候选报告")
    if body.baseline_run_id and body.mode != "multi":
        raise HTTPException(422, "single 技术对照不能再引用候选基线")
    output, baseline, independent = _evaluation_run_locations(body)
    authorization = request.headers.get("Authorization", "")
    result = await _execute_evaluation_run(body, output, baseline, independent, authorization)
    actor = getattr(request.state, "fap_actor", None) or "human"
    await audit(
        "eval.run.completed",
        agent_name="fleet-eval-01",
        actor=actor,
        detail={
            "run_id": result["run_id"],
            "mode": body.mode,
            "status": result["status"],
            "accepted_goal": result["accepted_goal"],
            "report_id": (result.get("report") or {}).get("report_id"),
            "auto_development_allowed": False,
        },
    )
    return {
        **result,
        "output_dir": str(output),
        "auto_development_allowed": False,
        "next_experiment_requires": "maintainer_accept",
    }
