"""Task 群聊通用只读投影（ADR-0031）。

不创建新会话实体：把既有 Task、LOOP、Thread 事件、评论和 OUTPUT 聚合为
所有客户端可复用的时间线。写入仍走 FAP 既有 operation。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from .db import pool
from . import pool as poolmod

router = APIRouter(tags=["task-conversation"])


def _iso(value: Any) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value or "")


def _payload(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "".join(
            str(part["text"]) if isinstance(part, dict) and "text" in part else _text(part)
            for part in value
        ).strip()
    if isinstance(value, dict):
        if "text" in value:
            return _text(value["text"])
        if "content" in value:
            return _text(value["content"])
        if "result" in value:
            return _text(value["result"])
        if "description" in value:
            return _text(value["description"])
    return ""


def content_text(value: Any) -> str:
    """将 run_events 的 JSON 文本／消息载荷转为可展示纯文本。"""
    return _text(_payload(value))


def normalize_event(row: dict[str, Any], loop: dict[str, Any]) -> dict[str, Any] | None:
    """把根 Thread 消息与原生 subagent task_* 事件转为统一时间线条目。"""
    event_type = str(row.get("event_type") or "")
    payload = _payload(row.get("content"))
    if event_type.startswith("task_"):
        content = _text(payload)
        if not content:
            return None
        kind = "subagent"
        actor = "subagent"
    elif event_type in ("llm.ai.response", "llm.human.input") or (
        isinstance(payload, dict) and payload.get("type") in ("ai", "human", "user")
    ):
        content = _text(payload)
        if not content:
            return None
        role = payload.get("type") if isinstance(payload, dict) else ""
        is_human = event_type == "llm.human.input" or role in ("human", "user")
        kind = "message"
        actor = "human" if is_human else str(loop.get("claimant") or "agent")
    else:
        return None
    return {
        "id": f"event:{row.get('thread_id')}:{row.get('seq')}",
        "kind": kind,
        "actor": actor,
        "content": content[:12000],
        "created_at": _iso(row.get("created_at")),
        "loop_id": loop.get("id"),
        "thread_id": row.get("thread_id"),
        "run_id": row.get("run_id"),
        "seq": row.get("seq"),
        "event_type": event_type,
    }


def build_conversation(detail: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    loops = detail.get("loops") or []
    participants = [
        {
            "id": f"{loop.get('claimant_type')}:{loop.get('claimant')}",
            "name": loop.get("claimant"),
            "type": loop.get("claimant_type"),
            "loop_id": loop.get("id"),
            "thread_id": loop.get("thread_id"),
            "status": loop.get("status"),
        }
        for loop in loops
    ]
    messages: list[dict[str, Any]] = []
    for row in events:
        loop = next((item for item in loops if item.get("thread_id") == row.get("thread_id")), None)
        if loop is None:
            continue
        message = normalize_event(row, loop)
        if message:
            messages.append(message)
    for comment in detail.get("comments") or []:
        messages.append({
            "id": f"comment:{comment.get('id')}",
            "kind": "comment",
            "actor": comment.get("author") or "human",
            "content": str(comment.get("content") or "")[:12000],
            "created_at": _iso(comment.get("created_at")),
            "loop_id": comment.get("loop_id"),
            "thread_id": None,
        })
    messages.sort(key=lambda item: (item["created_at"], item["id"]))
    outputs = [
        {
            "id": f"output:{loop.get('id')}:v{loop.get('version')}",
            "loop_id": loop.get("id"),
            "actor": loop.get("claimant"),
            "content": loop.get("deliverable"),
            "status": loop.get("status"),
            "version": loop.get("version"),
            "created_at": _iso(loop.get("delivered_at") or loop.get("updated_at")),
            "thread_id": loop.get("thread_id"),
        }
        for loop in loops if loop.get("deliverable")
    ]
    return {
        "task": detail["task"],
        "participants": participants,
        "messages": messages,
        "outputs": outputs,
        "children": detail.get("children") or [],
        "loops": loops,
    }


@router.get("/api/tasks/{task_id}/conversation")
async def task_conversation(task_id: int) -> dict[str, Any]:
    detail = await poolmod.task_detail(task_id)
    if detail is None:
        raise HTTPException(404, f"task {task_id} not found")
    thread_ids = [loop["thread_id"] for loop in detail["loops"] if loop.get("thread_id")]
    events: list[dict[str, Any]] = []
    if thread_ids:
        async with (await pool()).acquire() as conn:
            rows = await conn.fetch(
                """SELECT e.thread_id,e.run_id,e.seq,e.event_type,e.content,e.created_at
                   FROM unnest($1::text[]) AS requested(thread_id)
                   CROSS JOIN LATERAL (
                     SELECT thread_id,run_id,seq,event_type,content,created_at
                     FROM run_events WHERE thread_id=requested.thread_id
                     ORDER BY seq DESC LIMIT 200
                   ) e""",
                thread_ids,
            )
        events = [dict(row) for row in rows]
    return build_conversation(detail, events)
