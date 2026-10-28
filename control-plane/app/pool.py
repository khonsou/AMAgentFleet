"""任务池（ADR-0010）：task = 验收单元，loop = 执行单元。

- task：open → review → done，只前进；任何已识别的人或 Agent 均可验收（ADR-0015）。
- loop：一次领取 = 一个 loop = 一条 thread；竞争非独占，同 task ≤3 个并行；
  状态 claimed → running → delivered ⇄ returned（打回只打 loop，同 thread 续跑）→ accepted/archived。
- 领取三途径（无暗路径）：人页面指派（create task 带 assignee）、人自领（claim human）、
  cron 默认由 YAML 所属 Agent 认领；显式 orchestrator 时由指定编排者认领（ADR-0016）。
- 兼容层：agents/{name}/dispatch 内部转为"建 task + 指派 loop"，旧行为不破。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import asyncpg

from .db import audit, pool
from .dispatcher import enqueue_loop_run

MAX_LOOPS_PER_TASK = 3


class PoolError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message


# ---------------------------------------------------------------- task 创建

async def create_task(
    input_text: str,
    *,
    source: str = "web",
    assignee: str | None = None,
    created_by: str = "human",
    parent_task_id: int | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """建 task（open）。assignee 给 agent 名 = 人指派途径：同时建 loop 并入队。"""
    async with (await pool()).acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO fleet_tasks (agent_name, input, source, parent_task_id, idempotency_key)
               VALUES ($1,$2,$3,$4,$5)
               ON CONFLICT (idempotency_key) DO NOTHING
               RETURNING *""",
            assignee or "unassigned", input_text, source, parent_task_id, idempotency_key,
        )
    if row is None:
        async with (await pool()).acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM fleet_tasks WHERE idempotency_key=$1", idempotency_key)
        return dict(row)
    task = dict(row)
    await audit("task.created", agent_name=assignee, actor=created_by,
                args={"input": input_text[:200]}, detail={"task_id": task["id"]})
    if assignee:
        await claim_loop(task["id"], "agent", assignee, created_by=created_by)
    return task


# ---------------------------------------------------------------- loop 领取

async def _count_loops(conn: asyncpg.Connection, task_id: int) -> int:
    """上限只计 agent loop——人领取不消耗平台 token，不占配额（ADR-0010 的成本 rationale）。"""
    return await conn.fetchval(
        "SELECT count(*) FROM fleet_task_loops WHERE task_id=$1 AND claimant_type='agent'", task_id)


async def claim_loop(task_id: int, claimant_type: str, claimant: str, *, created_by: str) -> dict[str, Any]:
    """领取 = 建 loop（竞争非独占，≤3）。agent 领取直接入队执行；human 领取等人交付。"""
    async with (await pool()).acquire() as conn:
        if claimant_type == "agent":
            # 全认领路径统一门禁（inbox flush / cron 认领 / FAP claim / 指派）：
            # 休眠/已销毁 agent 不可被唤醒执行，与 dispatch/inbox 端点同口径。
            status = await conn.fetchval(
                "SELECT status FROM fleet_agents WHERE name=$1", claimant)
            if status is None:
                raise PoolError(404, f"agent not registered: {claimant}")
            if status not in ("registered", "running"):
                raise PoolError(409, f"agent '{claimant}' is {status}; start it before dispatching")
        task = await conn.fetchrow("SELECT id, pool_status FROM fleet_tasks WHERE id=$1 FOR UPDATE", task_id)
        if task is None:
            raise PoolError(404, f"task {task_id} not found")
        if task["pool_status"] == "done":
            raise PoolError(409, f"task {task_id} already done")
        n = await _count_loops(conn, task_id)
        if n >= MAX_LOOPS_PER_TASK:
            raise PoolError(409, f"task {task_id} 已达 {MAX_LOOPS_PER_TASK} 个 loop 上限")
        row = await conn.fetchrow(
            """INSERT INTO fleet_task_loops (task_id, claimant_type, claimant, created_by)
               VALUES ($1,$2,$3,$4) RETURNING *""",
            task_id, claimant_type, claimant, created_by,
        )
        # 认领即占用：agent → 其名；human → 'human'。任何认领后 task 不再 'unassigned'
        # （池过滤/展示统一依赖此语义——人 agent 一视同仁，ADR-0010）
        await conn.execute(
            "UPDATE fleet_tasks SET agent_name=$2 WHERE id=$1 AND agent_name='unassigned'",
            task_id, claimant if claimant_type == "agent" else "human",
        )
    loop = dict(row)
    await audit("loop.claimed", agent_name=claimant if claimant_type == "agent" else None,
                actor=created_by, detail={"loop_id": loop["id"], "task_id": task_id,
                                          "claimant": f"{claimant_type}:{claimant}"})
    if claimant_type == "agent":
        await enqueue_loop_run(loop["id"])
    return loop


# ---------------------------------------------------------------- 交付/验收

async def deliver_loop(loop_id: int, content: str, *, tokens: int | None = None,
                       thread_id: str | None = None, by_system: bool = True) -> None:
    async with (await pool()).acquire() as conn:
        await conn.execute(
            """UPDATE fleet_task_loops SET status='delivered', deliverable=$2, tokens=COALESCE($3,tokens),
                      thread_id=COALESCE($4,thread_id), delivered_at=now(), updated_at=now()
               WHERE id=$1""",
            loop_id, content[:4000], tokens, thread_id,
        )
        task_id = await conn.fetchval("SELECT task_id FROM fleet_task_loops WHERE id=$1", loop_id)
        # status（执行口径）与 pool_status（验收口径）联动：交付即执行成功，
        # 验收状态由 pool_status 表达（人机同一条路，统一 2026-10-10 修 #8）。
        await conn.execute(
            """UPDATE fleet_tasks SET status='success', pool_status='review', updated_at=now()
               WHERE id=$1 AND pool_status='open'""",
            task_id,
        )
    await audit("loop.delivered", actor="system" if by_system else "human",
                detail={"loop_id": loop_id, "task_id": task_id})


async def human_deliver(loop_id: int, content: str, *, author: str) -> None:
    """人交付：与 agent 产出同构（ADR-0010 人机同构）。"""
    await deliver_loop(loop_id, content, by_system=False)
    await add_comment(await _task_of(loop_id), author=author, content=content, loop_id=loop_id)


async def return_loop(loop_id: int, comment: str, *, author: str) -> None:
    """打回 loop（不动 task）：写评论 + 状态 returned → 同 thread 续跑。"""
    async with (await pool()).acquire() as conn:
        loop = await conn.fetchrow(
            "UPDATE fleet_task_loops SET status='returned', version=version+1, updated_at=now() WHERE id=$1 RETURNING task_id",
            loop_id,
        )
        if loop is None:
            raise PoolError(404, f"loop {loop_id} not found")
    await add_comment(loop["task_id"], author=author, content=comment, loop_id=loop_id)
    await audit("loop.returned", actor=author, detail={"loop_id": loop_id})
    async with (await pool()).acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM fleet_task_loops WHERE id=$1", loop_id)
    # agent 的 loop 自动同 thread 续跑；human 的 loop 等人修正后重新交付（deliver 接口）
    if row["claimant_type"] == "agent":
        await enqueue_loop_run(loop_id, resume_comment=comment)


async def accept_loop(task_id: int, loop_id: int, *, author: str) -> None:
    """验收采纳：task → done；该 loop accepted，其余 archived。"""
    async with (await pool()).acquire() as conn:
        ok = await conn.fetchval(
            """UPDATE fleet_tasks SET pool_status='done', accepted_loop_id=$2, updated_at=now()
               WHERE id=$1 AND pool_status='review' RETURNING id""",
            task_id, loop_id,
        )
        if ok is None:
            raise PoolError(409, f"task {task_id} 不在 review 态（可能已被验收或已关闭）")
        await conn.execute(
            """UPDATE fleet_task_loops SET status='archived', updated_at=now()
               WHERE task_id=$1 AND id != $2 AND status != 'accepted'""",
            task_id, loop_id,
        )
        await conn.execute(
            "UPDATE fleet_task_loops SET status='accepted', updated_at=now() WHERE id=$1", loop_id)
        await conn.execute(
            "UPDATE fleet_tasks SET status='success', finished_at=now() WHERE id=$1", task_id)
    await audit("task.accepted", actor=author, detail={"task_id": task_id, "loop_id": loop_id})


async def accept_pending_review(*, author: str) -> dict[str, Any]:
    """一次调用验收当前全部 review Task，避免 Agent 逐条循环及失控收尾。

    每个 Task 采纳最新 delivered Loop；没有交付 Loop 的项明确列入 skipped。
    整批在同一事务中完成，返回已验收明细及剩余 review 数。
    """
    accepted: list[dict[str, int]] = []
    skipped: list[dict[str, Any]] = []
    async with (await pool()).acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """SELECT t.id AS task_id,
                          (SELECT l.id FROM fleet_task_loops l
                           WHERE l.task_id=t.id AND l.status='delivered'
                           ORDER BY l.id DESC LIMIT 1) AS loop_id
                   FROM fleet_tasks t WHERE t.pool_status='review'
                   ORDER BY t.id FOR UPDATE""")
            for row in rows:
                task_id, loop_id = row["task_id"], row["loop_id"]
                if loop_id is None:
                    skipped.append({"task_id": task_id, "reason": "no_delivered_loop"})
                    continue
                await conn.execute(
                    """UPDATE fleet_task_loops SET status='archived', updated_at=now()
                       WHERE task_id=$1 AND id != $2 AND status != 'accepted'""",
                    task_id, loop_id)
                await conn.execute(
                    "UPDATE fleet_task_loops SET status='accepted', updated_at=now() WHERE id=$1",
                    loop_id)
                await conn.execute(
                    """UPDATE fleet_tasks SET pool_status='done', status='success',
                       accepted_loop_id=$2, finished_at=now(), updated_at=now() WHERE id=$1""",
                    task_id, loop_id)
                accepted.append({"task_id": task_id, "loop_id": loop_id})
            remaining = await conn.fetchval(
                "SELECT count(*) FROM fleet_tasks WHERE pool_status='review'")
    for item in accepted:
        await audit("task.accepted", actor=author, detail=item)
    return {"accepted": accepted, "skipped": skipped,
            "accepted_count": len(accepted), "remaining_review": remaining}


async def _task_of(loop_id: int) -> int:
    async with (await pool()).acquire() as conn:
        return await conn.fetchval("SELECT task_id FROM fleet_task_loops WHERE id=$1", loop_id)


async def add_comment(task_id: int, *, author: str, content: str, loop_id: int | None = None) -> None:
    async with (await pool()).acquire() as conn:
        await conn.execute(
            "INSERT INTO fleet_task_comments (task_id, loop_id, author, content) VALUES ($1,$2,$3,$4)",
            task_id, loop_id, author, content,
        )


# ---------------------------------------------------------------- 查询

async def task_detail(task_id: int) -> dict[str, Any] | None:
    async with (await pool()).acquire() as conn:
        task = await conn.fetchrow("SELECT * FROM fleet_tasks WHERE id=$1", task_id)
        if task is None:
            return None
        loops = await conn.fetch("SELECT * FROM fleet_task_loops WHERE task_id=$1 ORDER BY id", task_id)
        comments = await conn.fetch("SELECT * FROM fleet_task_comments WHERE task_id=$1 ORDER BY id", task_id)
    return {"task": dict(task), "loops": [dict(l) for l in loops],
            "comments": [dict(c) for c in comments]}


async def pending_review() -> list[dict[str, Any]]:
    async with (await pool()).acquire() as conn:
        rows = await conn.fetch("SELECT * FROM fleet_tasks WHERE pool_status='review' ORDER BY id DESC LIMIT 50")
    return [dict(r) for r in rows]
