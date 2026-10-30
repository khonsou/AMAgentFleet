"""Fleet Run 异常分类与 checkpoint 读取（不改变 Task/Loop 业务状态）。"""

from __future__ import annotations

import httpx

from .db import GATEWAY

TRANSIENT_HTTP_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}


def classify_failure(error: str, *, status_code: int | None = None,
                     transport: bool = False) -> str:
    """返回 recursion / transient / permanent；只有明确的临时故障才自动重试。"""
    if "recursion limit" in error.lower() or "graphrecursionerror" in error.lower():
        return "recursion"
    if transport or status_code in TRANSIENT_HTTP_STATUSES:
        return "transient"
    return "permanent"


async def latest_checkpoint(thread_id: str) -> str | None:
    """读取 Thread 最新 checkpoint；无法读取时由调用方决定降级为人工恢复。"""
    async with httpx.AsyncClient(base_url=GATEWAY, timeout=30, trust_env=False) as client:
        response = await client.get(f"/api/threads/{thread_id}/state")
    if response.status_code != 200:
        return None
    checkpoint_id = response.json().get("checkpoint_id")
    return checkpoint_id if isinstance(checkpoint_id, str) and checkpoint_id else None
