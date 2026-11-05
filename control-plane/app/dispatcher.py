"""Dispatcher（池化版，ADR-0010）：消费 loop 而非 task。

- 队列内存化：enqueue_loop_run 投递 loop_id；常驻 consumer 串行执行
  （生产按 runner 池扩多消费者；单消费者下竞争 loop 依次执行）。
- loop 生命周期：claimed →（消费）running → delivered（交付自动捕获）；
  异常 → 回 claimed 记 error（可重跑）；超时 → 回 claimed（崩溃遗留，重投递幂等）。
- 协作纪律（ADR-0004）不变：深度 ≤3 沿 task.parent_task_id 链；同链 agent 重复即拒环。
- 打回续跑：resume_comment 随 Loop 落库，重启/拒收不丢；成功交付后清除。
- 启动恢复 claimed/returned；running 周期心跳，reaper 窗口至少 2×run 超时。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone

import asyncpg
import httpx

from .db import audit, pool
from .recovery import classify_failure, latest_checkpoint
from .runtime import RunRequest, RuntimeServiceError, get_runtime

logger = logging.getLogger("fleet.dispatcher")

MAX_DEPTH = 3
RUN_TIMEOUT = 30 * 60
RUN_RECURSION_LIMIT = max(1, int(os.environ.get("FLEET_RUN_RECURSION_LIMIT", "300")))
MAX_AUTO_RETRIES = 2
MAX_AUTO_CONTINUATIONS = 1
MAX_CONTINUATIONS = 3
REAPER_TIMEOUT = max(
    2 * RUN_TIMEOUT,
    int(os.environ.get("FLEET_REAPER_TIMEOUT", str(2 * RUN_TIMEOUT))),
)
HEARTBEAT_INTERVAL = max(1, int(os.environ.get("FLEET_HEARTBEAT_INTERVAL", "60")))

# 原样 input=None 会把模型送回导致递归上限的同一工具循环。恢复指令只补充
# 收敛语义，不重复原始任务，也不要求重新规划已完成的副作用。
RECOVERY_INSTRUCTION = """【异常恢复指令】上次执行触及递归上限。基于 checkpoint 中已有消息和工具结果继续，不要重新执行原始任务，不要重复任何已完成的读取或探测。先判断现有信息是否足够：足够则立即交付；缺少的能力、权限或必要信息已反复不可获得，则停止调用工具，交付真实阻塞原因；仍有明确未完成步骤时，只执行必要的下一步。禁止再次读取 system.protocol 或 system.openapi 寻找替代能力，禁止仅改变 idempotency_key、query 等参数重试同一读取。"""

_QUEUE: asyncio.Queue[int] = asyncio.Queue()
_consumers_started = False
_background_enabled = False
_worker_tasks: set[asyncio.Task] = set()
WORKERS = max(1, int(os.environ.get("FLEET_DISPATCHER_WORKERS", "3")))  # 并行执行竞争 loop

_THREAD_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def set_background_enabled(enabled: bool) -> None:
    """由 startup 的单例门闩结果统一控制懒启动。"""
    global _background_enabled
    _background_enabled = enabled


def _start_consumers() -> None:
    global _consumers_started
    if _consumers_started or not _background_enabled:
        return
    _consumers_started = True
    loop = asyncio.get_running_loop()
    for _ in range(WORKERS):
        task = loop.create_task(_consumer())
        _worker_tasks.add(task)
        task.add_done_callback(_worker_tasks.discard)
    logger.info("dispatcher started: %s consumer(s)", WORKERS)


async def enqueue_loop_run(loop_id: int) -> None:
    """领取/打回后入队；standby 可保留队列，但不会启动本地消费者。"""
    await _QUEUE.put(loop_id)
    _start_consumers()


# ---------------------------------------------------------------- 执行

async def _chain(task_id: int) -> list[tuple[int, str | None]]:
    chain: list[tuple[int, str | None]] = []
    async with (await pool()).acquire() as conn:
        cur = task_id
        for _ in range(MAX_DEPTH + 2):
            row = await conn.fetchrow(
                "SELECT id, parent_task_id, agent_name FROM fleet_tasks WHERE id=$1", cur)
            if row is None:
                break
            chain.append((row["id"], row["agent_name"]))
            cur = row["parent_task_id"]
            if cur is None:
                break
    return chain


async def _run_loop(loop_id: int) -> None:
    async with (await pool()).acquire() as conn:
        async with conn.transaction():
            loop = await conn.fetchrow(
                "SELECT * FROM fleet_task_loops WHERE id=$1 FOR UPDATE", loop_id)
            if loop is None or loop["status"] not in ("claimed", "returned"):
                return
            if loop["claimant_type"] != "agent":
                return  # human loop 不由 dispatcher 执行
            task = await conn.fetchrow("SELECT * FROM fleet_tasks WHERE id=$1", loop["task_id"])
            recovery_action = loop.get("recovery_action")
            # 消费恢复动作：运行中的 Loop 不会被延迟队列或人工操作重复启动。
            await conn.execute(
                """UPDATE fleet_task_loops SET status='running', running_action=$2,
                   recovery_action=NULL, next_retry_at=NULL, updated_at=now() WHERE id=$1""",
                loop_id, recovery_action)
    task = dict(task)
    task_id = task["id"]
    agent = loop["claimant"]

    chain = await _chain(task_id)
    depth = len(chain) - 1
    if depth > MAX_DEPTH:
        await _fail_loop(loop_id, f"thread tree depth > {MAX_DEPTH}", "permanent")
        await audit("loop.rejected", agent_name=agent, actor="dispatcher",
                    detail={"loop_id": loop_id, "reason": "depth"})
        return
    # 拒环只看非人执行者重复（同一条链上同一 agent 出现两次 = 环；人可重复协作）
    agents = [a for _, a in chain if a not in ("human", "unassigned")]
    if len(agents) != len(set(agents)):
        await _fail_loop(loop_id, "collaboration cycle detected", "permanent")
        await audit("loop.rejected", agent_name=agent, actor="dispatcher",
                    detail={"loop_id": loop_id, "reason": "cycle", "chain": agents})
        return

    input_text = task["input"]
    if loop["resume_comment"]:
        input_text = (
            f"{input_text}\n\n【验收退回意见】{loop['resume_comment']}\n请修正后重新交付。")
    # 起跑前即确定 thread_id 并落库：执行中途 agent_spawn 靠它反解父 task 挂树
    # （若等跑完才从网关返回写入，指挥官 run 中途 spawn 时反解必落空）。
    thread_id = loop["thread_id"] or str(uuid.uuid4())
    if not loop["thread_id"]:
        async with (await pool()).acquire() as conn:
            await conn.execute(
                "UPDATE fleet_task_loops SET thread_id=$2 WHERE id=$1", loop_id, thread_id)
    resuming = recovery_action == "resume" and bool(loop["last_checkpoint_id"])
    request = RunRequest(
        agent_name=agent,
        thread_id=thread_id,
        loop_id=loop_id,
        input_text=input_text,
        model_profile=loop.get("model_profile"),
        recovery_action=recovery_action,
        checkpoint_ref=loop.get("last_checkpoint_id"),
        recursion_limit=loop.get("recursion_limit") or RUN_RECURSION_LIMIT,
        subagent_enabled=True,
    )

    heartbeat = asyncio.create_task(_heartbeat(loop_id))
    try:
        runtime = get_runtime()
        execute = runtime.resume_run if resuming else runtime.execute_run
        result = await execute(
            request, client_factory=httpx.AsyncClient,
            recovery_instruction=RECOVERY_INSTRUCTION,
        )
        tokens = result.tokens
        preview = result.text
        artifacts = _extract_artifacts(result.artifacts, thread_id)
        if artifacts:
            preview = (preview or "") + "\n\n📎 产物：\n" + "\n".join(f"- {a}" for a in artifacts)
        # 交付自动捕获 → task 入 review。状态守卫放在 UPDATE 内，避免
        # “先查状态再交付”与验收／归档并发时重新复活 Loop。
        from . import pool as poolmod
        try:
            await poolmod.deliver_loop(
                loop_id, preview or "(无文本交付)", tokens=tokens, thread_id=thread_id)
        except poolmod.PoolError as exc:
            await audit("loop.late_result_dropped",
                        detail={"loop_id": loop_id, "reason": exc.message})
            return
        await audit("loop.completed", agent_name=agent, tokens=tokens,
                    detail={"loop_id": loop_id, "task_id": task_id, "thread_id": thread_id,
                            "resumed": resuming, "recursion_limit": RUN_RECURSION_LIMIT})
    except RuntimeServiceError as exc:
        error = str(exc)[:500]
        await _fail_loop(loop_id, error, exc.kind, thread_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("loop run failed: %s", loop_id)
        error = str(exc)[:500]
        kind = classify_failure(error, transport=isinstance(exc, httpx.TransportError))
        await _fail_loop(loop_id, error, kind, thread_id)
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)


async def _heartbeat(loop_id: int) -> None:
    """长任务运行期间续租；仅 running 可更新，交付/归档后不再触碰。"""
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        try:
            async with (await pool()).acquire() as conn:
                await conn.execute(
                    "UPDATE fleet_task_loops SET updated_at=now() WHERE id=$1 AND status='running'",
                    loop_id)
        except Exception:  # noqa: BLE001
            # 心跳故障不中断网关执行；reaper 窗口仍兜底真正失联的 Loop。
            logger.exception("loop heartbeat failed: %s", loop_id)


async def _reset_loop(loop_id: int, error: str, failure_kind: str = "permanent",
                      checkpoint_id: str | None = None):
    """异常回 claimed，并原子计算一次性恢复动作；终态 Loop 不得复活。"""
    async with (await pool()).acquire() as conn:
        return await conn.fetchrow(
            """UPDATE fleet_task_loops SET status='claimed', error=$2, failure_kind=$3,
                   running_action=NULL, last_checkpoint_id=COALESCE($4,last_checkpoint_id),
                   retry_count=CASE
                     WHEN $3='transient' AND retry_count<2 THEN retry_count+1
                     ELSE retry_count END,
                   continuation_count=CASE
                     WHEN $3='recursion' AND $4 IS NOT NULL AND continuation_count<1
                     THEN continuation_count+1 ELSE continuation_count END,
                   recovery_action=CASE
                     WHEN $3='recursion' AND $4 IS NOT NULL AND continuation_count<1 THEN 'resume'
                     WHEN $3='transient' AND retry_count<2 THEN 'retry'
                     ELSE NULL END,
                   next_retry_at=CASE
                     WHEN $3='recursion' AND $4 IS NOT NULL AND continuation_count<1 THEN now()
                     WHEN $3='transient' AND retry_count<2
                     THEN now()+(CASE retry_count WHEN 0 THEN '2 seconds' ELSE '10 seconds' END)::interval
                     ELSE NULL END,
                   updated_at=now()
               WHERE id=$1 AND status IN ('claimed','running','returned')
               RETURNING id,recovery_action,next_retry_at""",
            loop_id, error, failure_kind, checkpoint_id,
        )


async def _delayed_enqueue(loop_id: int, delay: float) -> None:
    if delay > 0:
        await asyncio.sleep(delay)
    await _QUEUE.put(loop_id)
    _start_consumers()


async def _schedule_recovery(loop_id: int, row) -> None:
    """按数据库中的持久化恢复动作调度；进程重启后由 recover_claimed 重新计算延迟。"""
    if not row or not row["recovery_action"]:
        return
    next_at = row["next_retry_at"]
    delay = max(0.0, (next_at - datetime.now(timezone.utc)).total_seconds()) if next_at else 0.0
    task = asyncio.create_task(_delayed_enqueue(loop_id, delay))
    _worker_tasks.add(task)
    task.add_done_callback(_worker_tasks.discard)
    await audit("loop.recovery_scheduled", actor="dispatcher",
                detail={"loop_id": loop_id, "action": row["recovery_action"],
                        "delay_seconds": round(delay, 3)})


async def _fail_loop(loop_id: int, error: str, failure_kind: str = "permanent",
                     thread_id: str | None = None) -> None:
    checkpoint_id = None
    if thread_id:
        try:
            checkpoint_id = await latest_checkpoint(thread_id)
        except Exception:  # noqa: BLE001
            logger.exception("failed to capture checkpoint for loop %s", loop_id)
    row = await _reset_loop(loop_id, error, failure_kind, checkpoint_id)
    await _schedule_recovery(loop_id, row)
    # Failed children are terminal too; parents must be resumed to report the
    # failure or deliver a degraded result instead of waiting forever.
    from . import pool as poolmod
    task_id = await poolmod._task_of(loop_id)
    await poolmod._resume_parent_when_children_settled(task_id)


def _extract_thread_id(result: dict) -> str | None:
    td = result.get("thread_data") or {}
    for key in ("workspace_path", "uploads_path"):
        m = _THREAD_UUID.search(str(td.get(key) or ""))
        if m:
            return m.group(0)
    return None


def _extract_artifacts(result: dict | list[str], thread_id: str | None) -> list[str]:
    """把 Runtime 返回的产物路径转换为 Console 同源下载链接。"""
    from urllib.parse import quote
    base = os.environ.get("FLEET_ARTIFACT_LINK_BASE", "").rstrip("/")
    arts = result.get("artifacts") or [] if isinstance(result, dict) else result
    links = []
    for p in arts:
        name = str(p).rsplit("/", 1)[-1]
        if thread_id:
            seg = quote(str(p).lstrip("/"), safe="/")
            links.append(f"{name}: {base}/api/console/threads/{thread_id}/artifacts/{seg}")
        else:
            links.append(name)
    return links


class RecoveryError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message


async def resume_loop(loop_id: int, *, actor: str = "human") -> dict[str, str]:
    """人工从 checkpoint 续跑；老失败 Loop 没有存 checkpoint 时按需向网关补取。"""
    async with (await pool()).acquire() as conn:
        loop = await conn.fetchrow("SELECT * FROM fleet_task_loops WHERE id=$1", loop_id)
    if loop is None or loop["claimant_type"] != "agent":
        raise RecoveryError(404, "agent loop 不存在")
    if loop["status"] not in ("claimed", "returned"):
        raise RecoveryError(409, f"loop 当前状态 {loop['status']} 不可续跑")
    if loop["recovery_action"]:
        raise RecoveryError(409, "loop 已有恢复动作排队，请勿重复启动")
    if loop["continuation_count"] >= MAX_CONTINUATIONS:
        raise RecoveryError(409, f"loop 已达到 {MAX_CONTINUATIONS} 次续跑上限，请重新执行或人工检查")
    if not loop["thread_id"]:
        raise RecoveryError(409, "loop 没有 Thread，无法断点续跑")
    try:
        checkpoint_id = loop["last_checkpoint_id"] or await latest_checkpoint(loop["thread_id"])
    except httpx.HTTPError as exc:
        raise RecoveryError(502, "暂时无法读取 Thread checkpoint，请稍后重试") from exc
    if not checkpoint_id:
        raise RecoveryError(409, "未找到可用 checkpoint，请使用重新执行")
    async with (await pool()).acquire() as conn:
        updated = await conn.fetchrow(
            """UPDATE fleet_task_loops SET status='claimed', recovery_action='resume',
                   last_checkpoint_id=$2, continuation_count=continuation_count+1,
                   next_retry_at=NULL, error=NULL, updated_at=now()
               WHERE id=$1 AND claimant_type='agent' AND status IN ('claimed','returned')
                 AND recovery_action IS NULL AND continuation_count<3
               RETURNING id""", loop_id, checkpoint_id)
    if updated is None:
        raise RecoveryError(409, "loop 状态已变化，请刷新后重试")
    await audit("loop.resumed", actor=actor,
                detail={"loop_id": loop_id, "checkpoint_id": checkpoint_id})
    await enqueue_loop_run(loop_id)
    return {"status": "claimed", "recovery_action": "resume", "checkpoint_id": checkpoint_id}


async def recover_claimed() -> int:
    """leader 启动恢复新 Loop 与待恢复 Loop；永久错误不在重启时自动重放。"""
    if not _background_enabled:
        return 0
    async with (await pool()).acquire() as conn:
        rows = await conn.fetch(
            """SELECT id,error,recovery_action,next_retry_at FROM fleet_task_loops
               WHERE claimant_type='agent' AND status IN ('claimed','returned')
                 AND (error IS NULL OR recovery_action IS NOT NULL)
               ORDER BY id""")
    for row in rows:
        if row["recovery_action"]:
            await _schedule_recovery(row["id"], row)
        else:
            await _QUEUE.put(row["id"])
    if any(not row["recovery_action"] for row in rows):
        _start_consumers()
    logger.info("dispatcher startup recovery: %s loop(s)", len(rows))
    return len(rows)


async def consumer_loop() -> None:
    """兼容入口：仅 leader 可启动 worker，standby 调用直接返回。"""
    if not _background_enabled:
        logger.warning("dispatcher consumer 未启动：当前实例未持有单例门闩")
        return
    _start_consumers()
    await asyncio.Future()  # 常驻


async def shutdown() -> None:
    """停止并回收本进程全部 worker；未入队 Loop 由下次启动恢复。"""
    global _consumers_started
    set_background_enabled(False)
    _consumers_started = False
    tasks = list(_worker_tasks)
    _worker_tasks.clear()
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    while True:
        try:
            _QUEUE.get_nowait()
        except asyncio.QueueEmpty:
            break


async def _consumer() -> None:
    while True:
        try:
            loop_id = await asyncio.wait_for(_QUEUE.get(), timeout=30)
        except asyncio.TimeoutError:
            loop_id = None
        if loop_id is not None:
            await _run_loop(loop_id)
            continue
        # 空闲时回收超时 running（崩溃遗留）→ 回 claimed 重投递（幂等）。
        # 窗口至少 2×run 超时，且运行心跳会持续刷新 updated_at。
        try:
            async with (await pool()).acquire() as conn:
                stale = await conn.fetch(
                    """UPDATE fleet_task_loops SET status='claimed',
                           recovery_action=COALESCE(running_action,'retry'), running_action=NULL,
                           next_retry_at=NULL, updated_at=now()
                       WHERE status='running' AND updated_at < now() - ($1 || ' seconds')::interval
                       RETURNING id,recovery_action,next_retry_at""", str(REAPER_TIMEOUT))
            for row in stale:
                await _schedule_recovery(row["id"], row)
        except Exception:  # noqa: BLE001
            logger.exception("reaper error")
