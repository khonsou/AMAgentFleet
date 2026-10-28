"""Inbox：挂在 agent 上的持久消息队列（§6.2 唤醒语义，借鉴 ACP Inbox）。

消息进 fleet_inbox（PG，跨 session 存活）；flush_loop 周期把每个 agent 的
pending 消息合并为一个 task 入池（source=inbox，池化后由领取驱动执行），
即"休眠 agent 被消息唤醒"= 池里多了一个等它领的任务。
"""

from __future__ import annotations

import asyncio
import logging

from .db import audit, pool
from .dispatcher import enqueue_loop_run
from . import pool as poolmod

logger = logging.getLogger("fleet.inbox")


async def post(agent_name: str, payload: str, *, source: str = "agent") -> int:
    async with (await pool()).acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO fleet_inbox (agent_name, payload, source) VALUES ($1,$2,$3) RETURNING id",
            agent_name, payload, source,
        )
    await audit("inbox.received", agent_name=agent_name, actor=source,
                args={"payload": payload[:200]}, detail={"inbox_id": row["id"]})
    return row["id"]


async def list_messages(agent_name: str, limit: int = 50) -> list[dict]:
    async with (await pool()).acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM fleet_inbox WHERE agent_name=$1 ORDER BY id DESC LIMIT $2",
            agent_name, limit)
    return [dict(r) for r in rows]


async def flush_all() -> int:
    """把所有有 pending 消息的 agent 各合并投递为一个入池 task。返回 flush 数。"""
    async with (await pool()).acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT agent_name FROM fleet_inbox WHERE status='pending'")
    n = 0
    for r in rows:
        n += await flush_agent(r["agent_name"])
    return n


async def flush_agent(agent_name: str) -> int:
    """消息状态、Task 与 Loop 原子提交；任何失败都由事务回滚为 pending。"""
    async with (await pool()).acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """UPDATE fleet_inbox SET status='dispatched', dispatched_at=now()
                   WHERE agent_name=$1 AND status='pending' RETURNING id, payload""",
                agent_name,
            )
            if not rows:
                return 0
            combined = "\n".join(f"[消息 {r['id']}] {r['payload']}" for r in rows)
            task, task_created = await poolmod._create_task_with_conn(
                conn, combined, source="inbox", assignee=agent_name,
                parent_task_id=None,
                idempotency_key=f"inbox:{agent_name}:" + "-".join(str(r["id"]) for r in rows),
            )
            loop = None
            loop_created = False
            if task_created or task["pool_status"] != "done":
                loop, loop_created = await poolmod._claim_loop_with_conn(
                    conn, task["id"], "agent", agent_name,
                    created_by="dispatcher", idempotent=True,
                )
    if task_created:
        await audit("task.created", agent_name=agent_name, actor="dispatcher",
                    args={"input": combined[:200]}, detail={"task_id": task["id"]})
    if loop_created:
        await audit("loop.claimed", agent_name=agent_name, actor="dispatcher",
                    detail={"loop_id": loop["id"], "task_id": task["id"],
                            "claimant": f"agent:{agent_name}"})
    if loop and loop["status"] in ("claimed", "returned"):
        await enqueue_loop_run(loop["id"])
    await audit("inbox.dispatched", agent_name=agent_name, actor="dispatcher",
                detail={"inbox_ids": [r["id"] for r in rows], "task_id": task["id"],
                        "merged": len(rows)})
    return 1


async def flush_loop(interval_seconds: float = 5.0) -> None:
    """常驻攒批循环：pending 消息按 agent 合并后唤醒（入池）。"""
    logger.info("inbox flush loop started (interval=%ss)", interval_seconds)
    while True:
        try:
            await flush_all()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("inbox flush error; continue")
        await asyncio.sleep(interval_seconds)
