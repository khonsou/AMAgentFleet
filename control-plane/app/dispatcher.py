"""Dispatcher（池化版，ADR-0010）：消费 loop 而非 task。

- 队列内存化：enqueue_loop_run 投递 loop_id；常驻 consumer 串行执行
  （生产按 runner 池扩多消费者；单消费者下竞争 loop 依次执行）。
- loop 生命周期：claimed →（消费）running → delivered（交付自动捕获）；
  异常 → 回 claimed 记 error（可重跑）；超时 → 回 claimed（崩溃遗留，重投递幂等）。
- 协作纪律（ADR-0004）不变：深度 ≤3 沿 task.parent_task_id 链；同链 agent 重复即拒环。
- 打回续跑：return_loop 以 resume_comment 重新入队，gateway 调用带同 thread_id 水合。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid

import asyncpg
import httpx

from .db import GATEWAY, audit, pool

logger = logging.getLogger("fleet.dispatcher")

MAX_DEPTH = 3
RUN_TIMEOUT = 30 * 60

_QUEUE: asyncio.Queue[int] = asyncio.Queue()
_consumers_started = False
WORKERS = max(1, int(os.environ.get("FLEET_DISPATCHER_WORKERS", "3")))  # 并行执行竞争 loop

_THREAD_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


async def enqueue_loop_run(loop_id: int, resume_comment: str | None = None) -> None:
    """领取/打回后入队。resume_comment 存内存映射，消费时追加为新的用户消息。"""
    global _consumers_started
    if resume_comment:
        _resume_comments[loop_id] = resume_comment
    await _QUEUE.put(loop_id)
    if not _consumers_started:
        _consumers_started = True
        loop = asyncio.get_running_loop()
        for i in range(WORKERS):
            loop.create_task(_consumer())
        logger.info("dispatcher started: %s consumer(s)", WORKERS)


_resume_comments: dict[int, str] = {}


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
        loop = await conn.fetchrow(
            "SELECT * FROM fleet_task_loops WHERE id=$1 FOR UPDATE", loop_id)
        if loop is None or loop["status"] not in ("claimed", "returned"):
            return
        if loop["claimant_type"] != "agent":
            return  # human loop 不由 dispatcher 执行
        task = await conn.fetchrow("SELECT * FROM fleet_tasks WHERE id=$1", loop["task_id"])
        await conn.execute(
            "UPDATE fleet_task_loops SET status='running', updated_at=now() WHERE id=$1", loop_id)
    task = dict(task)
    task_id = task["id"]
    agent = loop["claimant"]

    chain = await _chain(task_id)
    depth = len(chain) - 1
    if depth > MAX_DEPTH:
        await _reset_loop(loop_id, f"thread tree depth > {MAX_DEPTH}")
        await audit("loop.rejected", agent_name=agent, actor="dispatcher",
                    detail={"loop_id": loop_id, "reason": "depth"})
        return
    # 拒环只看非人执行者重复（同一条链上同一 agent 出现两次 = 环；人可重复协作）
    agents = [a for _, a in chain if a not in ("human", "unassigned")]
    if len(agents) != len(set(agents)):
        await _reset_loop(loop_id, "collaboration cycle detected")
        await audit("loop.rejected", agent_name=agent, actor="dispatcher",
                    detail={"loop_id": loop_id, "reason": "cycle", "chain": agents})
        return

    input_text = task["input"]
    if loop_id in _resume_comments:
        input_text = f"{input_text}\n\n【验收退回意见】{_resume_comments.pop(loop_id)}\n请修正后重新交付。"
    # 起跑前即确定 thread_id 并落库：执行中途 agent_spawn 靠它反解父 task 挂树
    # （若等跑完才从网关返回写入，指挥官 run 中途 spawn 时反解必落空）。
    thread_id = loop["thread_id"] or str(uuid.uuid4())
    if not loop["thread_id"]:
        async with (await pool()).acquire() as conn:
            await conn.execute(
                "UPDATE fleet_task_loops SET thread_id=$2 WHERE id=$1", loop_id, thread_id)
    body: dict = {
        "assistant_id": agent,
        "input": {"messages": [{"role": "user", "content": input_text}]},
        "on_disconnect": "continue",
        # subagent_enabled：fleet run 默认开 in-thread subagent（task 工具）。
        # per-agent 开关 = agent YAML allowed_subagents（[] = 硬拒，见 DeerFlow
        # lead_agent 解析：subagent_enabled = requested and allowed != []）。
        "config": {"configurable": {"thread_id": thread_id, "subagent_enabled": True}},
    }

    try:
        async with httpx.AsyncClient(base_url=GATEWAY, timeout=RUN_TIMEOUT + 60, trust_env=False) as client:
            r = await client.post("/api/runs/wait", json=body)
        if r.status_code != 200:
            await _reset_loop(loop_id, f"gateway {r.status_code}: {r.text[:300]}")
            return
        result = r.json()
        if result.get("status") == "error":
            await _reset_loop(loop_id, str(result.get("error"))[:500])
            return
        tokens = _extract_tokens(result)
        thread_id = loop["thread_id"] or _extract_thread_id(result)
        preview = _extract_preview(result)
        artifacts = _extract_artifacts(result, thread_id)
        if artifacts:
            preview = (preview or "") + "\n\n📎 产物：\n" + "\n".join(f"- {a}" for a in artifacts)
        # 交付自动捕获 → task 入 review
        from . import pool as poolmod
        # 并发保护：若 task 已验收（done）或 loop 已被归档，丢弃迟到结果
        async with (await pool()).acquire() as conn:
            cur = await conn.fetchrow("SELECT status, task_id FROM fleet_task_loops WHERE id=$1", loop_id)
            task_ps = await conn.fetchval("SELECT pool_status FROM fleet_tasks WHERE id=$1", cur["task_id"])
        if cur["status"] in ("accepted", "archived") or task_ps == "done":
            await audit("loop.late_result_dropped", detail={"loop_id": loop_id})
            return
        await poolmod.deliver_loop(loop_id, preview or "(无文本交付)", tokens=tokens, thread_id=thread_id)
        await audit("loop.completed", agent_name=agent, tokens=tokens,
                    detail={"loop_id": loop_id, "task_id": task_id, "thread_id": thread_id})
    except Exception as exc:  # noqa: BLE001
        logger.exception("loop run failed: %s", loop_id)
        await _reset_loop(loop_id, str(exc)[:500])


async def _reset_loop(loop_id: int, error: str) -> None:
    """异常/拒绝：回 claimed（可重跑），error 落库。"""
    async with (await pool()).acquire() as conn:
        await conn.execute(
            "UPDATE fleet_task_loops SET status='claimed', error=$2, updated_at=now() WHERE id=$1",
            loop_id, error,
        )


def _extract_tokens(result: dict) -> int | None:
    total, found = 0, False
    for msg in result.get("messages") or []:
        rm = msg.get("response_metadata") or {}
        usage = rm.get("token_usage") or rm.get("usage") or {}
        if usage.get("total_tokens"):
            total += usage["total_tokens"]
            found = True
    return total if found else None


def _extract_thread_id(result: dict) -> str | None:
    td = result.get("thread_data") or {}
    for key in ("workspace_path", "uploads_path"):
        m = _THREAD_UUID.search(str(td.get(key) or ""))
        if m:
            return m.group(0)
    return None


def _extract_artifacts(result: dict, thread_id: str | None) -> list[str]:
    """产物链接：agent 经 present_files 呈现的文件 → Console artifacts 代理地址
    （FLEET_ARTIFACT_LINK_BASE 可配；生产指向公网 Console，网关不出 VPC）。"""
    from urllib.parse import quote
    base = os.environ.get("FLEET_ARTIFACT_LINK_BASE", "http://127.0.0.1:8002")
    arts = result.get("artifacts") or []
    links = []
    for p in arts:
        name = str(p).rsplit("/", 1)[-1]
        if thread_id:
            # 网关 artifacts 是路径段路由（无前导斜杠）：/artifacts/mnt/user-data/outputs/x.md
            seg = quote(str(p).lstrip("/"), safe="/")
            links.append(f"{name}: {base}/api/console/threads/{thread_id}/artifacts/{seg}")
        else:
            links.append(name)
    return links


def _extract_preview(result: dict, limit: int = 3900) -> str | None:
    for msg in reversed(result.get("messages") or []):
        if msg.get("type") != "ai":
            continue
        c = msg.get("content")
        if isinstance(c, list):
            c = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in c)
        if isinstance(c, str) and c.strip():
            return c.strip()[:limit]
    return None


async def consumer_loop() -> None:
    """兼容入口：启动全部 worker（首单领取时也会懒启动）。"""
    global _consumers_started
    if not _consumers_started:
        _consumers_started = True
        loop = asyncio.get_running_loop()
        for i in range(WORKERS):
            loop.create_task(_consumer())
        logger.info("dispatcher started: %s consumer(s)", WORKERS)
    await asyncio.Future()  # 常驻


async def _consumer() -> None:
    while True:
        try:
            loop_id = await asyncio.wait_for(_QUEUE.get(), timeout=30)
        except asyncio.TimeoutError:
            loop_id = None
        if loop_id is not None:
            await _run_loop(loop_id)
            continue
        # 空闲时回收超时 running（崩溃遗留）→ 回 claimed 重投递（幂等）
        try:
            async with (await pool()).acquire() as conn:
                stale = await conn.fetch(
                    """UPDATE fleet_task_loops SET status='claimed', updated_at=now()
                       WHERE status='running' AND updated_at < now() - ($1 || ' seconds')::interval
                       RETURNING id""", str(RUN_TIMEOUT))
            for row in stale:
                await _QUEUE.put(row["id"])
        except Exception:  # noqa: BLE001
            logger.exception("reaper error")
