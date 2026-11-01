"""Stable Thread/Run API surface backed by the configured RuntimeAdapter."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from .runtime import RunRequest, RuntimeServiceError, get_runtime

router = APIRouter(tags=["runtime"])


async def _invoke(awaitable):
    try:
        return await awaitable
    except RuntimeServiceError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


class CreateThreadBody(BaseModel):
    instruction: str | None = None
    assistant_id: str = "lead_agent"
    recursion_limit: int = Field(default=300, ge=1)


class MessageBody(BaseModel):
    content: str
    recursion_limit: int = Field(default=300, ge=1)
    assistant_id: str | None = None


@router.get("/api/runtime/capabilities")
async def runtime_capabilities():
    runtime = get_runtime()
    return {"runtime": runtime.name, "capabilities": runtime.capabilities().__dict__}


@router.get("/api/console/overview")
async def overview():
    return await _invoke(get_runtime().get_overview())


@router.get("/api/console/fleet")
async def fleet():
    return await _invoke(get_runtime().list_threads())


@router.get("/api/console/threads/{tid}")
async def thread_detail(tid: str):
    return await _invoke(get_runtime().get_thread(tid))


@router.get("/api/console/threads/{tid}/messages")
async def thread_messages(tid: str):
    return await _invoke(get_runtime().get_messages(tid))


@router.post("/api/console/threads")
async def create_thread(body: CreateThreadBody):
    return await _invoke(get_runtime().create_thread(
        body.assistant_id, body.instruction, body.recursion_limit))


@router.post("/api/console/threads/{tid}/messages")
async def send_message(tid: str, body: MessageBody):
    return await _invoke(get_runtime().send_message(
        tid, body.content, body.assistant_id, body.recursion_limit))


@router.delete("/api/console/threads/{tid}")
async def delete_thread(tid: str):
    return await _invoke(get_runtime().delete_thread(tid))


@router.get("/api/console/threads/{tid}/artifacts/{path:path}")
async def artifact(tid: str, path: str):
    content, media_type = await _invoke(get_runtime().get_artifact(tid, path))
    return Response(content=content, media_type=media_type)


@router.get("/api/threads/{thread_id}/state")
async def thread_state(thread_id: str):
    return await _invoke(get_runtime().get_thread_state(thread_id))


@router.get("/api/threads/{thread_id}/token-usage")
async def token_usage(thread_id: str):
    return await _invoke(get_runtime().get_token_usage(thread_id))


@router.post("/api/runs/wait")
async def run_wait(body: dict[str, Any]):
    config = body.get("config") or {}
    configurable = config.get("configurable") or {}
    messages = (body.get("input") or {}).get("messages") or []
    content = messages[-1].get("content", "") if messages else ""
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    metadata = body.get("metadata") or {}
    checkpoint = body.get("checkpoint_id")
    recovery = metadata.get("fleet_recovery") or ("resume" if checkpoint else None)
    result = await _invoke(get_runtime().execute_run(RunRequest(
        agent_name=body["assistant_id"],
        thread_id=configurable["thread_id"],
        loop_id=int(metadata.get("fleet_loop_id") or 0),
        input_text=content,
        model_profile=(body.get("context") or {}).get("model_name"),
        recovery_action=recovery,
        checkpoint_ref=checkpoint,
        recursion_limit=int(config.get("recursion_limit") or 300),
        subagent_enabled=bool(configurable.get("subagent_enabled", True)),
    ), recovery_instruction=content if recovery == "resume" else ""))
    return {
        "status": result.status, "messages": result.messages,
        "artifacts": result.artifacts, "run_id": result.run_id,
        "checkpoint_id": result.checkpoint_ref,
    }


@router.get("/api/threads/{thread_id}/runs/{run_id}")
async def run_detail(thread_id: str, run_id: str):
    return await _invoke(get_runtime().get_run(thread_id, run_id))


@router.get("/api/runs/{run_id}/messages")
async def run_messages(run_id: str, limit: int = 50, before_seq: int | None = None, after_seq: int | None = None):
    return await _invoke(get_runtime().get_run_messages(run_id, {
        "limit": limit, "before_seq": before_seq, "after_seq": after_seq,
    }))


@router.get("/api/threads/{thread_id}/runs/{run_id}/events")
async def run_events(thread_id: str, run_id: str):
    return await _invoke(get_runtime().get_run_events(thread_id, run_id))


@router.post("/api/threads/{thread_id}/runs/{run_id}/cancel")
async def run_cancel(thread_id: str, run_id: str):
    return await _invoke(get_runtime().cancel_run(thread_id, run_id))


@router.put("/api/threads/{thread_id}/runs/{run_id}/feedback")
async def feedback_put(thread_id: str, run_id: str, body: dict[str, Any] | None = None):
    return await _invoke(get_runtime().feedback("PUT", thread_id, run_id, body=body))


@router.post("/api/threads/{thread_id}/runs/{run_id}/feedback")
async def feedback_create(thread_id: str, run_id: str, body: dict[str, Any] | None = None):
    return await _invoke(get_runtime().feedback("POST", thread_id, run_id, body=body))


@router.get("/api/threads/{thread_id}/runs/{run_id}/feedback")
async def feedback_list(thread_id: str, run_id: str):
    return await _invoke(get_runtime().feedback("GET", thread_id, run_id))


@router.get("/api/threads/{thread_id}/runs/{run_id}/feedback/stats")
async def feedback_stats(thread_id: str, run_id: str):
    return await _invoke(get_runtime().feedback("GET", thread_id, run_id, feedback_id="stats"))


@router.delete("/api/threads/{thread_id}/runs/{run_id}/feedback")
async def feedback_delete(thread_id: str, run_id: str):
    return await _invoke(get_runtime().feedback("DELETE", thread_id, run_id))


@router.delete("/api/threads/{thread_id}/runs/{run_id}/feedback/{feedback_id}")
async def feedback_delete_by_id(thread_id: str, run_id: str, feedback_id: str):
    return await _invoke(get_runtime().feedback("DELETE", thread_id, run_id, feedback_id=feedback_id))
