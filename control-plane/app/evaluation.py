"""Platform evaluation report persistence and bounded recommendation API (ADR-0035)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

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
            updated = await conn.fetchrow(
                """UPDATE fleet_eval_recommendations SET status=$2, updated_at=now()
                   WHERE id=$1 RETURNING *""",
                recommendation_id, status,
            )
    await audit(
        "eval.recommendation_transition",
        agent_name="fleet-eval-01",
        actor=actor,
        detail={"recommendation_id": recommendation_id, "from": row["status"], "to": status},
    )
    return _report(dict(updated))
