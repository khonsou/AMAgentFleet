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
import logging
from typing import Any

import asyncpg

from .db import audit, pool
from .dispatcher import enqueue_loop_run
from .execution import enrich_execution

logger = logging.getLogger("fleet.pool")
MAX_LOOPS_PER_TASK = 3


class PoolError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message


# ---------------------------------------------------------------- task 创建

async def _create_task_with_conn(
    conn: asyncpg.Connection,
    input_text: str,
    *,
    source: str,
    assignee: str | None,
    parent_task_id: int | None,
    idempotency_key: str | None,
    model_profile: str | None = None,
    subagent_model_policy: str = "auto",
) -> tuple[dict[str, Any], bool]:
    """在调用方事务内创建或按幂等键取回 Task，返回（Task，是否新建）。

    固定继承策略沿任务树传播，不能被后代静默覆盖。
    """
    if subagent_model_policy not in ("auto", "inherit"):
        raise PoolError(422, "subagent_model_policy 仅支持 auto / inherit")
    if subagent_model_policy == "inherit" and not model_profile and not parent_task_id:
        raise PoolError(422, "固定继承模型时必须显式指定 model_profile")
    if parent_task_id:
        parent = await conn.fetchrow(
            "SELECT model_profile, subagent_model_policy FROM fleet_tasks WHERE id=$1",
            parent_task_id)
        if parent is None:
            raise PoolError(404, f"parent task {parent_task_id} not found")
        if parent["subagent_model_policy"] == "inherit":
            inherited = parent["model_profile"]
            if model_profile and model_profile != inherited:
                raise PoolError(403, f"父 Task 固定继承模型 {inherited}，禁止覆盖为 {model_profile}")
            model_profile = inherited
            subagent_model_policy = "inherit"
    if subagent_model_policy == "inherit" and not model_profile:
        raise PoolError(422, "父 Task 未锁定模型，不能使用固定继承策略")
    row = await conn.fetchrow(
        """INSERT INTO fleet_tasks
           (agent_name, input, source, parent_task_id, idempotency_key,
            model_profile, subagent_model_policy)
           VALUES ($1,$2,$3,$4,$5,$6,$7)
           ON CONFLICT (idempotency_key) DO NOTHING
           RETURNING *""",
        assignee or "unassigned", input_text, source, parent_task_id, idempotency_key,
        model_profile, subagent_model_policy,
    )
    if row is not None:
        return dict(row), True
    row = await conn.fetchrow(
        "SELECT * FROM fleet_tasks WHERE idempotency_key=$1", idempotency_key)
    return dict(row), False


async def create_task(
    input_text: str,
    *,
    source: str = "web",
    assignee: str | None = None,
    created_by: str = "human",
    parent_task_id: int | None = None,
    idempotency_key: str | None = None,
    model_profile: str | None = None,
    subagent_model_policy: str = "auto",
    recursion_limit: int | None = None,
    summary_chars: int = 500,
) -> dict[str, Any]:
    """建 Task 并可指派 Agent；模型继承、创建和认领在同一事务内完成。"""
    loop = None
    async with (await pool()).acquire() as conn:
        async with conn.transaction():
            task, task_created = await _create_task_with_conn(
                conn, input_text, source=source, assignee=assignee,
                parent_task_id=parent_task_id, idempotency_key=idempotency_key,
                model_profile=model_profile, subagent_model_policy=subagent_model_policy,
            )
            loop_created = False
            if assignee and (task_created or task["pool_status"] != "done"):
                loop, loop_created = await _claim_loop_with_conn(
                    conn, task["id"], "agent", assignee,
                    created_by=created_by, idempotent=True,
                    model_profile=model_profile,
                    recursion_limit=recursion_limit, summary_chars=summary_chars,
                )
    if task_created:
        await audit("task.created", agent_name=assignee, actor=created_by,
                    args={"input": input_text[:200]},
                    detail={"task_id": task["id"],
                            "model_profile": task.get("model_profile"),
                            "subagent_model_policy": task.get("subagent_model_policy")})
    if loop_created:
        await audit("loop.claimed", agent_name=assignee, actor=created_by,
                    detail={"loop_id": loop["id"], "task_id": task["id"],
                            "claimant": f"agent:{assignee}",
                            "model_profile": loop.get("model_profile"),
                            "model_selected_by": loop.get("model_selected_by")})
    if loop and loop["status"] in ("claimed", "returned"):
        await enqueue_loop_run(loop["id"])
    return task


# ---------------------------------------------------------------- loop 领取

async def _count_loops(conn: asyncpg.Connection, task_id: int) -> int:
    """上限只计 agent loop——人领取不消耗平台 token，不占配额（ADR-0010 的成本 rationale）。"""
    return await conn.fetchval(
        "SELECT count(*) FROM fleet_task_loops WHERE task_id=$1 AND claimant_type='agent'", task_id)


async def _claim_loop_with_conn(
    conn: asyncpg.Connection,
    task_id: int,
    claimant_type: str,
    claimant: str,
    *,
    created_by: str,
    model_profile: str | None = None,
    idempotent: bool = False,
    recursion_limit: int | None = None,
    summary_chars: int = 500,
) -> tuple[dict[str, Any], bool]:
    """在调用方事务内认领 Loop；agent Loop 在入库前锁定不含秘密的模型快照。"""
    from . import models

    if claimant_type == "agent":
        # 全认领路径统一门禁（inbox flush / cron 认领 / FAP claim / 指派）：
        # 休眠/已销毁 agent 不可被唤醒执行，与 dispatch/inbox 端点同口径。
        status = await conn.fetchval(
            "SELECT status FROM fleet_agents WHERE name=$1", claimant)
        if status is None:
            raise PoolError(404, f"agent not registered: {claimant}")
        if status not in ("registered", "running"):
            raise PoolError(409, f"agent '{claimant}' is {status}; start it before dispatching")
    task = await conn.fetchrow("SELECT * FROM fleet_tasks WHERE id=$1 FOR UPDATE", task_id)
    if task is None:
        raise PoolError(404, f"task {task_id} not found")
    if task["pool_status"] == "done":
        raise PoolError(409, f"task {task_id} already done")
    task_profile = task.get("model_profile")
    if model_profile and task_profile and model_profile != task_profile:
        raise PoolError(409, f"Task 已指定模型 {task_profile}，认领时不能覆盖为 {model_profile}")
    existing = await conn.fetchrow(
        """SELECT * FROM fleet_task_loops
           WHERE task_id=$1 AND claimant_type=$2 AND claimant=$3""",
        task_id, claimant_type, claimant,
    )
    if existing is not None:
        if idempotent:
            return dict(existing), False
        raise PoolError(409, f"{claimant_type} '{claimant}' 已认领过 task {task_id}")
    n = await _count_loops(conn, task_id)
    if claimant_type == "agent" and n >= MAX_LOOPS_PER_TASK:
        raise PoolError(409, f"task {task_id} 已达 {MAX_LOOPS_PER_TASK} 个 loop 上限")
    snapshot = None
    selected_by = None
    if claimant_type == "agent":
        try:
            model_profile, snapshot, selected_by = await models.resolve_profile(
                claimant, model_profile or task_profile, selected_by=created_by)
        except models.ModelError as exc:
            raise PoolError(exc.status, exc.message) from exc
    if recursion_limit is not None and not 1 <= recursion_limit <= 300:
        raise PoolError(422, "recursion_limit must be between 1 and 300")
    if not 100 <= summary_chars <= 1000:
        raise PoolError(422, "summary_chars must be between 100 and 1000")
    row = await conn.fetchrow(
        """INSERT INTO fleet_task_loops
           (task_id, claimant_type, claimant, created_by,
            model_profile, model_snapshot, model_selected_by, recursion_limit, summary_chars)
           VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9) RETURNING *""",
        task_id, claimant_type, claimant, created_by,
        model_profile, json.dumps(snapshot) if snapshot else None, selected_by,
        recursion_limit or 300, summary_chars,
    )
    # 认领即占用：agent → 其名；human → 'human'。任何认领后 task 不再 'unassigned'
    # （池过滤/展示统一依赖此语义——人 agent 一视同仁，ADR-0010）
    await conn.execute(
        "UPDATE fleet_tasks SET agent_name=$2 WHERE id=$1 AND agent_name='unassigned'",
        task_id, claimant if claimant_type == "agent" else "human",
    )
    return dict(row), True


async def claim_loop(
    task_id: int,
    claimant_type: str,
    claimant: str,
    *,
    created_by: str,
    model_profile: str | None = None,
    recursion_limit: int | None = None,
    summary_chars: int = 500,
) -> dict[str, Any]:
    """领取 = 建 loop（竞争非独占，≤3）。agent 领取直接入队执行；human 领取等人交付。"""
    async with (await pool()).acquire() as conn:
        async with conn.transaction():
            loop, _ = await _claim_loop_with_conn(
                conn, task_id, claimant_type, claimant, created_by=created_by,
                model_profile=model_profile, recursion_limit=recursion_limit,
                summary_chars=summary_chars)
    await audit("loop.claimed", agent_name=claimant if claimant_type == "agent" else None,
                actor=created_by, detail={"loop_id": loop["id"], "task_id": task_id,
                                          "claimant": f"{claimant_type}:{claimant}",
                                          "model_profile": loop.get("model_profile"),
                                          "model_selected_by": loop.get("model_selected_by")})
    if claimant_type == "agent":
        await enqueue_loop_run(loop["id"])
    return loop


# ---------------------------------------------------------------- 交付/验收

async def deliver_loop(loop_id: int, content: str, *, tokens: int | None = None,
                       thread_id: str | None = None, by_system: bool = True) -> None:
    """最终交付：清除等待标记并进入验收；迟到结果仍由状态门闩拒绝。"""
    async with (await pool()).acquire() as conn:
        async with conn.transaction():
            updated = await conn.fetchrow(
                """UPDATE fleet_task_loops SET status='delivered', deliverable=$2,
                          tokens=COALESCE($3,tokens), thread_id=COALESCE($4,thread_id),
                          waiting_for_children=false,
                          error=NULL, failure_kind=NULL, recovery_action=NULL, running_action=NULL,
                          next_retry_at=NULL, retry_count=0,
                          resume_comment=NULL, delivered_at=now(), updated_at=now()
                   WHERE id=$1 AND status IN ('claimed','running','returned')
                   RETURNING task_id""",
                loop_id, content[:4000], tokens, thread_id,
            )
            if updated is None:
                current = await conn.fetchrow(
                    "SELECT task_id, status FROM fleet_task_loops WHERE id=$1", loop_id)
                if current is None:
                    raise PoolError(404, f"loop {loop_id} not found")
                raise PoolError(409, f"loop {loop_id} 当前状态 {current['status']} 不可交付")
            task_id = updated["task_id"]
            # status（执行口径）与 pool_status（验收口径）联动：交付即执行成功，
            # 验收状态由 pool_status 表达（人机同一条路，统一 2026-10-10 修 #8）。
            await conn.execute(
                """UPDATE fleet_tasks SET status='success', pool_status='review', updated_at=now()
                   WHERE id=$1 AND pool_status='open'""",
                task_id,
            )
    await audit("loop.delivered", actor="system" if by_system else "human",
                detail={"loop_id": loop_id, "task_id": task_id})
    await _resume_parent_when_children_settled(task_id)


async def wait_loop_for_children(loop_id: int, content: str | None = None, *,
                                 tokens: int | None = None, thread_id: str | None = None) -> None:
    """保存中间 OUTPUT，但 Task 保持 open，等待直接子任务终态后再续跑。"""
    async with (await pool()).acquire() as conn:
        async with conn.transaction():
            updated = await conn.fetchrow(
                """UPDATE fleet_task_loops SET status='delivered',
                          deliverable=COALESCE($2,deliverable),
                          tokens=COALESCE($3,tokens), thread_id=COALESCE($4,thread_id),
                          waiting_for_children=true,
                          error=NULL, failure_kind=NULL, recovery_action=NULL, running_action=NULL,
                          next_retry_at=NULL, retry_count=0,
                          resume_comment=NULL, delivered_at=COALESCE(delivered_at,now()), updated_at=now()
                   WHERE id=$1 AND status IN ('claimed','running','returned')
                   RETURNING task_id""",
                loop_id, content[:4000] if content is not None else None, tokens, thread_id,
            )
            if updated is None:
                current = await conn.fetchrow(
                    "SELECT task_id, status FROM fleet_task_loops WHERE id=$1", loop_id)
                if current is None:
                    raise PoolError(404, f"loop {loop_id} not found")
                raise PoolError(409, f"loop {loop_id} 当前状态 {current['status']} 不可等待")
            task_id = updated["task_id"]
            await conn.execute(
                """UPDATE fleet_tasks SET status='running', pool_status='open', updated_at=now()
                   WHERE id=$1 AND pool_status='open'""",
                task_id,
            )
    await audit("loop.waiting_children",
                detail={"loop_id": loop_id, "task_id": task_id})


async def has_pending_children(task_id: int) -> bool:
    """与父子唤醒使用同一终态口径；没有直接子任务时返回 false。"""
    async with (await pool()).acquire() as conn:
        pending = await conn.fetchval(
            """SELECT EXISTS(
                 SELECT 1 FROM fleet_tasks t
                 JOIN LATERAL (
                   SELECT status, error, recovery_action FROM fleet_task_loops
                   WHERE task_id=t.id ORDER BY id DESC LIMIT 1
                 ) l ON TRUE
                 WHERE t.parent_task_id=$1
                   AND (CASE
                     WHEN l.status IN ('error','rejected')
                       OR (l.error IS NOT NULL AND l.recovery_action IS NULL) THEN 'error'
                     ELSE t.status
                   END) NOT IN ('success','error','rejected')
               )""",
            task_id,
        )
    return bool(pending)


def bounded_summary(content: Any, limit: int) -> str:
    """Compress parent-facing output to the child Loop's explicit character budget."""
    text = " ".join(str(content).split())
    if len(text) <= limit:
        return text
    cut = text[: max(1, limit - 1)]
    boundary = max(cut.rfind("。"), cut.rfind("；"), cut.rfind(". "), cut.rfind("\n"))
    if boundary >= int(limit * 0.6):
        cut = cut[:boundary]
    return cut.rstrip() + "…"


async def _resume_parent_when_children_settled(task_id: int) -> None:
    """全部直接子 Task 终态后，以真实 OUTPUT 唤醒等待中的父 Loop。

    true 是本批显式等待；NULL 保留既有提前交付记录的兼容行为。最终交付的
    false、正在执行、已 returned 或已验收的父 Loop 不重复触发。
    """
    terminal = ("success", "error", "rejected")
    async with (await pool()).acquire() as conn:
        parent_task_id = await conn.fetchval(
            "SELECT parent_task_id FROM fleet_tasks WHERE id=$1", task_id)
        if parent_task_id is None:
            return
        children = await conn.fetch(
            """SELECT t.id, t.agent_name,
                      CASE
                        WHEN l.status IN ('error','rejected')
                          OR (l.error IS NOT NULL AND l.recovery_action IS NULL) THEN 'error'
                        ELSE t.status
                      END AS status,
                      l.id AS loop_id, l.deliverable, l.error, l.summary_chars
               FROM fleet_tasks t
               JOIN LATERAL (
                   SELECT id, status, deliverable, error, recovery_action, summary_chars FROM fleet_task_loops
                   WHERE task_id=t.id ORDER BY id DESC LIMIT 1
               ) l ON TRUE
               WHERE t.parent_task_id=$1 ORDER BY t.id""",
            parent_task_id,
        )
        logger.warning(
            "parent resume check: task=%s parent=%s child_states=%s",
            task_id, parent_task_id, [row["status"] for row in children],
        )
        if not children or any(row["status"] not in terminal for row in children):
            return
        parent_loop = await conn.fetchrow(
            """SELECT id FROM fleet_task_loops
               WHERE task_id=$1 AND status='delivered'
                 AND waiting_for_children IS NOT FALSE
               ORDER BY id DESC LIMIT 1""",
            parent_task_id,
        )
    if parent_loop is None:
        logger.warning("parent resume skipped: parent=%s has no delivered Loop", parent_task_id)
        return
    logger.warning(
        "parent resume triggered: parent=%s loop=%s children=%s",
        parent_task_id, parent_loop["id"], [row["id"] for row in children],
    )
    lines = [
        "【全部直接子任务已终态】请基于以下真实结果继续原任务：核对来源与数字、去重合并、"
        "纠正矛盾并交付最终答案；存在失败或缺口必须明确说明。不得重复派发相同子任务，"
        "不得只复述分工计划。"
    ]
    for child in children:
        content = child["deliverable"] or child["error"] or "(无交付)"
        summary = bounded_summary(content, child.get("summary_chars") or 500)
        lines.append(
            f"- Task #{child['id']} / Loop #{child['loop_id']} / "
            f"{child['agent_name']} / {child['status']}: {summary}"
        )
    try:
        await return_loop(
            parent_loop["id"], "\n".join(lines), author="system:child-completion")
    except PoolError as exc:
        if exc.status != 409:
            raise
        logger.warning("parent resume conflict ignored: parent=%s loop=%s", parent_task_id, parent_loop["id"])
        # 另一个子任务或人工验收已先改变父 Loop；不重复唤醒。


async def human_deliver(loop_id: int, content: str, *, author: str) -> None:
    """人交付：与 agent 产出同构（ADR-0010 人机同构）。"""
    await deliver_loop(loop_id, content, by_system=False)
    await add_comment(await _task_of(loop_id), author=author, content=content, loop_id=loop_id)


async def return_loop(loop_id: int, comment: str, *, author: str) -> None:
    """仅 delivered Loop 可打回；意见随 returned 状态原子落库，续跑成功后清除。"""
    async with (await pool()).acquire() as conn:
        async with conn.transaction():
            loop = await conn.fetchrow(
                """UPDATE fleet_task_loops
                   SET status='returned', version=version+1, resume_comment=$2,
                       waiting_for_children=false, updated_at=now()
                   WHERE id=$1 AND status='delivered' RETURNING task_id""",
                loop_id, comment,
            )
            if loop is None:
                current = await conn.fetchrow(
                    "SELECT status FROM fleet_task_loops WHERE id=$1", loop_id)
                if current is None:
                    raise PoolError(404, f"loop {loop_id} not found")
                raise PoolError(409, f"loop {loop_id} 当前状态 {current['status']} 不可打回")
    await add_comment(loop["task_id"], author=author, content=comment, loop_id=loop_id)
    await audit("loop.returned", actor=author, detail={"loop_id": loop_id})
    async with (await pool()).acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM fleet_task_loops WHERE id=$1", loop_id)
    # agent 的 loop 自动同 thread 续跑；human 的 loop 等人修正后重新交付（deliver 接口）
    if row["claimant_type"] == "agent":
        await enqueue_loop_run(loop_id)


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
        children = await conn.fetch(
            """SELECT t.*, l.id AS latest_loop_id, l.status AS latest_loop_status,
                      l.version AS latest_loop_version, l.error AS loop_error,
                      l.recovery_action, l.next_retry_at, l.failure_kind, l.waiting_for_children
               FROM fleet_tasks t
               LEFT JOIN LATERAL (
                 SELECT id, status, version, error, recovery_action, next_retry_at, failure_kind, waiting_for_children
                 FROM fleet_task_loops WHERE task_id=t.id ORDER BY id DESC LIMIT 1
               ) l ON true
               WHERE t.parent_task_id=$1 ORDER BY t.id""", task_id)
    return {"task": dict(task), "loops": [enrich_execution(dict(l)) for l in loops],
            "comments": [dict(c) for c in comments],
            "children": [enrich_execution(dict(child)) for child in children]}


WAIT_TARGETS = {"delivered", "all_delivered", "terminal", "version_gt", "orchestration_complete"}


def _orchestration_state(detail: dict[str, Any]) -> dict[str, Any]:
    children = detail.get("children") or []
    loops = detail.get("loops") or []
    delivered = [loop for loop in loops if loop.get("status") in ("delivered", "accepted")
                 and loop.get("waiting_for_children") is not True]
    final_version = max((int(loop.get("version") or 1) for loop in delivered), default=0)
    terminal_statuses = {"success", "error", "rejected", "closed", "cancelled", "failed"}
    terminal_pools = {"review", "done", "closed"}
    children_terminal = all(
        child.get("execution_status") in {"succeeded", "failed", "closed"}
        or child.get("status") in terminal_statuses
        or child.get("pool_status") in terminal_pools
        for child in children
    )
    return {
        "waiting_children": not children_terminal,
        "children_terminal": children_terminal,
        "root_delivered": bool(delivered),
        "final_delivery_version": final_version,
        "wake_count": max(0, final_version - 1) if children else 0,
    }


def _wait_selection(detail: dict[str, Any], loop_ids: set[int] | None) -> list[dict[str, Any]]:
    loops = detail.get("loops") or []
    if loop_ids is None:
        return loops
    selected = [loop for loop in loops if loop["id"] in loop_ids]
    missing = sorted(loop_ids - {loop["id"] for loop in selected})
    if missing:
        raise PoolError(404, f"task {detail['task']['id']} 不存在 Loop: {missing}")
    return selected


def _evaluate_wait(
    detail: dict[str, Any], *, target: str, loop_ids: set[int] | None,
    version_gt: int | None,
) -> tuple[str | None, str]:
    """返回（结束状态, 原因）；None 表示条件尚未满足，应继续有界等待。"""
    task = detail["task"]
    loops = _wait_selection(detail, loop_ids)
    if task.get("status") in ("error", "rejected") and not (
            target == "terminal" and task.get("pool_status") == "done"):
        return "failed", f"Task 已终止：{task.get('status')}"
    failed = [loop for loop in loops if loop.get("execution_status") == "failed"]
    if failed:
        return "failed", f"Loop {failed[0]['id']} 执行失败：{failed[0].get('last_error') or '未知错误'}"
    if target == "terminal":
        if task.get("pool_status") == "done" or task.get("status") in ("error", "rejected"):
            return "condition_met", "Task 已进入终态"
        if loops and all(loop.get("is_terminal") for loop in loops):
            return "condition_met", "指定 Loop 已全部进入执行终态"
    if target == "orchestration_complete":
        state = _orchestration_state(detail)
        if (
            detail.get("children") and state["root_delivered"] and state["children_terminal"]
            and state["wake_count"] >= 1
        ):
            return "condition_met", "直接子任务已终态，父任务已自动唤醒并再次交付"
        return None, (
            f"编排未完成：children_terminal={state['children_terminal']}，"
            f"root_delivered={state['root_delivered']}，wake_count={state['wake_count']}"
        )
    delivered = [loop for loop in loops if loop.get("status") in ("delivered", "accepted")
                 and loop.get("waiting_for_children") is not True]
    if target == "delivered" and delivered:
        return "condition_met", f"Loop {delivered[0]['id']} 已交付"
    if target == "all_delivered" and loops and len(delivered) == len(loops):
        return "condition_met", f"{len(loops)} 个指定 Loop 已全部交付"
    if target == "version_gt":
        newer = [loop for loop in delivered if int(loop.get("version") or 1) > version_gt]
        if newer:
            return "condition_met", f"Loop {newer[0]['id']} 已交付 version={newer[0]['version']}"
    return None, f"条件尚未满足：target={target}，已交付 {len(delivered)}/{len(loops)}"


async def wait_task(
    task_id: int, *, target: str, loop_ids: set[int] | None = None,
    version_gt: int | None = None, timeout_seconds: float = 60,
) -> dict[str, Any]:
    """服务端有界等待；timeout／failed 均不得被调用方解释为交付成功。"""
    if target not in WAIT_TARGETS:
        raise PoolError(422, f"target 仅支持 {sorted(WAIT_TARGETS)}")
    if target == "version_gt" and (version_gt is None or version_gt < 0):
        raise PoolError(422, "target=version_gt 时必须提供 version_gt >= 0")
    if loop_ids is not None and not loop_ids:
        raise PoolError(422, "loop_ids 不能为空")
    if not 0 <= timeout_seconds <= 120:
        raise PoolError(422, "timeout_seconds 必须在 0–120 秒之间")
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        detail = await task_detail(task_id)
        if detail is None:
            raise PoolError(404, f"task {task_id} not found")
        wait_status, reason = _evaluate_wait(
            detail, target=target, loop_ids=loop_ids, version_gt=version_gt)
        remaining = deadline - asyncio.get_running_loop().time()
        if wait_status is None and remaining <= 0:
            wait_status = "timeout"
            reason = f"等待超时（{timeout_seconds:g}s）：{reason}"
        if wait_status is not None:
            return {
                "wait_status": wait_status,
                "reason": reason,
                "orchestration": _orchestration_state(detail),
                **detail,
            }
        await asyncio.sleep(min(1.0, remaining))


async def pending_review() -> list[dict[str, Any]]:
    async with (await pool()).acquire() as conn:
        rows = await conn.fetch("SELECT * FROM fleet_tasks WHERE pool_status='review' ORDER BY id DESC LIMIT 50")
    return [dict(r) for r in rows]
