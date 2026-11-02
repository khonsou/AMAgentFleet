"""Runtime Capability Plane API (ADR-0042)."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from evals.platform.execution import FleetClient

from .capability_plane import CapabilityError, CapabilityService, PgStore
from .db import audit

router = APIRouter(tags=["runtime-capability-plane"])


def _agent_client(authorization: str) -> FleetClient:
    pat = authorization[7:].strip() if authorization.startswith("Bearer ") else None
    return FleetClient(
        os.environ.get("FLEET_CONTROL_PLANE_SELF_URL")
        or os.environ.get("FLEET_API")
        or "http://127.0.0.1:8003",
        pat,
        timeout=30,
    )


service = CapabilityService(
    PgStore(),
    allow_mock=os.environ.get("FLEET_ALLOW_MOCK_PROVIDER") == "1",
    agent_client_factory=_agent_client,
)


class ResolveInput(BaseModel):
    operation_id: str | None = None
    input_artifacts: list[str] = Field(default_factory=list)
    output_artifact: str | None = None
    risk_max: str = "high_risk"


class InvokeInput(BaseModel):
    capability_id: str
    input_value: Any = Field(alias="input")
    confirmed: bool = False


class PlanInput(BaseModel):
    plan: dict[str, Any]


async def _call(function, *args, **kwargs):
    try:
        return await function(*args, **kwargs)
    except CapabilityError as exc:
        raise HTTPException(exc.status, {"code": exc.code, "message": exc.message, "details": exc.details}) from exc


def _actor(request: Request) -> str:
    return getattr(request.state, "fap_actor", None) or "runtime-user"


@router.post("/api/capability-providers", status_code=201)
async def register_provider(body: dict[str, Any], request: Request):
    result = await _call(service.register_provider, body)
    await audit(
        "capability.provider_registered",
        agent_name=body.get("provider_id"),
        actor=_actor(request),
        args={"provider_id": body.get("provider_id"), "provider_type": body.get("provider_type")},
        detail={"config_version": result["config_version"]},
    )
    return result


@router.get("/api/capability-providers")
async def list_providers():
    return await _call(service.list_providers)


@router.get("/api/capability-providers/{provider_id}/card")
async def provider_card(provider_id: str):
    return await _call(service.provider_card, provider_id)


@router.post("/api/capability-providers/{provider_id}/verify")
async def verify_provider(provider_id: str, request: Request):
    result = await _call(service.verify_provider, provider_id)
    await audit(
        "capability.provider_verified",
        agent_name=provider_id,
        actor=_actor(request),
        detail={"health": result["health"]},
    )
    return result


@router.get("/api/capabilities")
async def list_capabilities():
    return await _call(service.list_capabilities)


@router.post("/api/capabilities/resolve")
async def resolve_capabilities(body: ResolveInput):
    return await _call(service.resolve, body.model_dump(exclude_none=True))


@router.post("/api/capabilities/invoke")
async def invoke_capability(body: InvokeInput, request: Request):
    return await _call(
        service.invoke,
        body.capability_id,
        body.input_value,
        confirmed=body.confirmed,
        authorization=request.headers.get("Authorization", ""),
        actor=_actor(request),
    )


@router.post("/api/workspaces", status_code=201)
async def create_workspace(body: dict[str, Any], request: Request):
    result = await _call(service.create_workspace, body)
    await audit(
        "capability.workspace_created",
        actor=_actor(request),
        detail={"workspace_id": result["id"], "goal": result["context"]["goal"][:200]},
    )
    return result


@router.get("/api/workspaces/{workspace_id}")
async def get_workspace(workspace_id: int):
    return await _call(service.workspace, workspace_id)


@router.post("/api/workspaces/{workspace_id}/evidence")
async def append_evidence(workspace_id: int, body: dict[str, Any]):
    return await _call(service.append_evidence, workspace_id, body)


@router.put("/api/workspaces/{workspace_id}/plan")
async def save_plan(workspace_id: int, body: PlanInput):
    return await _call(service.save_plan, workspace_id, body.plan)


@router.post("/api/workspaces/{workspace_id}/execute")
async def execute_plan(workspace_id: int, request: Request):
    return await _call(
        service.execute_plan,
        workspace_id,
        authorization=request.headers.get("Authorization", ""),
        actor=_actor(request),
    )
