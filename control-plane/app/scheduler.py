"""Scheduler（ADR-0016）：cron 触发 = 建池任务，默认由所属 Agent 认领。

只有单条 cron 的 orchestrator 或显式 FLEET_COMMANDER_AGENT 才指定编排者。
不再使用网关 scheduled-tasks 执行（首次运行时退役 gateway 侧 fleet:* 任务，
避免双触发）。触发判定：croniter 算上一个应触发点 > last_fired 即投递；
每 90s 扫描一轮，进程重启后凭 last_fired 续投（不漏不重）。
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

import httpx
from croniter import croniter
from zoneinfo import ZoneInfo

from .db import GATEWAY, audit, pool
from .dispatcher import enqueue_loop_run
from .registry import RegistryError, load_all_yaml

logger = logging.getLogger("fleet.scheduler")

SYNC_INTERVAL_SECONDS = 90
DEFAULT_ORCHESTRATOR = os.environ.get("FLEET_COMMANDER_AGENT")
_retired_gateway_crons = False


def _cron_specs(doc: dict) -> list[dict]:
    triggers = doc.get("triggers") or {}
    out = []
    for i, item in enumerate(triggers.get("cron") or []):
        if isinstance(item, str):
            out.append({"cron": item, "timezone": "Asia/Shanghai", "prompt": "", "orchestrator": None})
        elif isinstance(item, dict):
            out.append({
                "cron": item["cron"],
                "timezone": item.get("timezone", "Asia/Shanghai"),
                "prompt": item.get("prompt", ""),
                "orchestrator": item.get("orchestrator"),
            })
    return out


async def _retire_gateway_crons() -> int:
    """删除网关侧 fleet:* scheduled tasks（池化前遗留），返回删除数。"""
    global _retired_gateway_crons
    if _retired_gateway_crons:
        return 0
    _retired_gateway_crons = True
    n = 0
    async with httpx.AsyncClient(base_url=GATEWAY, timeout=30, trust_env=False) as client:
        tasks = (await client.get("/api/scheduled-tasks")).json()
        for t in tasks:
            if str(t.get("title", "")).startswith("fleet:"):
                r = await client.delete(f"/api/scheduled-tasks/{t['id']}")
                if r.status_code in (200, 204):
                    n += 1
    if n:
        logger.info("retired %s gateway fleet cron(s) (pool-ified)", n)
    return n


async def sync(actor: str = "scheduler") -> dict:
    """扫描一轮：Task、Loop 与 last_fired 同事务提交，提交后才入队。"""
    from . import pool as poolmod

    retired = await _retire_gateway_crons()
    docs = load_all_yaml()
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    fired: list[int] = []

    for name, doc in docs.items():
        for i, spec in enumerate(_cron_specs(doc)):
            prev = croniter(spec["cron"], now).get_prev(datetime)
            claimant = spec["orchestrator"] or DEFAULT_ORCHESTRATOR or name
            prompt = spec["prompt"] or f"[fleet cron] {name} 定时任务 {i}"
            async with (await pool()).acquire() as conn:
                try:
                    async with conn.transaction():
                        # 即使误开多个调度实例，同一条 cron 的同一轮也串行处理；
                        # 常驻单例门闩由 ADR-0012 后续批次补齐。
                        await conn.fetchval(
                            "SELECT pg_advisory_xact_lock(hashtext($1))",
                            f"fleet:cron:{name}:{i}",
                        )
                        last = await conn.fetchval(
                            "SELECT last_fired_at FROM fleet_cron_fires WHERE agent_name=$1 AND cron_idx=$2",
                            name, i)
                        if last is None:
                            # 首次见到该 cron：以最近一个已过的触发点为基线，不补投历史。
                            await conn.execute(
                                """INSERT INTO fleet_cron_fires (agent_name, cron_idx, last_fired_at)
                                   VALUES ($1,$2,$3)
                                   ON CONFLICT (agent_name, cron_idx) DO UPDATE SET last_fired_at=EXCLUDED.last_fired_at""",
                                name, i, prev,
                            )
                            continue
                        if last >= prev:
                            continue
                        task, task_created = await poolmod._create_task_with_conn(
                            conn, prompt, source="cron", assignee=None,
                            parent_task_id=None,
                            idempotency_key=f"cron:{name}:{i}:{prev.isoformat()}",
                        )
                        loop = None
                        loop_created = False
                        if task_created or task["pool_status"] != "done":
                            loop, loop_created = await poolmod._claim_loop_with_conn(
                                conn, task["id"], "agent", claimant,
                                created_by="scheduler", idempotent=True,
                            )
                        await conn.execute(
                            """INSERT INTO fleet_cron_fires (agent_name, cron_idx, last_fired_at)
                               VALUES ($1,$2,$3)
                               ON CONFLICT (agent_name, cron_idx) DO UPDATE SET last_fired_at=EXCLUDED.last_fired_at""",
                            name, i, prev,
                        )
                except poolmod.PoolError as exc:
                    # 任一步失败都回滚：下次扫描重试，不留下无 Loop 的 Task 或触发标记。
                    logger.warning(
                        "cron dispatch rolled back (agent=%s, cron=%s, claimant=%s): %s",
                        name, i, claimant, exc.message)
                    continue
            if task_created:
                await audit("task.created", actor="scheduler",
                            args={"input": prompt[:200]}, detail={"task_id": task["id"]})
            if loop_created:
                await audit("loop.claimed", agent_name=claimant, actor="scheduler",
                            detail={"loop_id": loop["id"], "task_id": task["id"],
                                    "claimant": f"agent:{claimant}"})
            if loop and loop["status"] in ("claimed", "returned"):
                await enqueue_loop_run(loop["id"])
            fired.append(task["id"])
            await audit("cron.fired", agent_name=name, actor=actor,
                        detail={"task_id": task["id"], "cron": spec["cron"],
                                "occurrence": prev.isoformat(),
                                "claimed_by": claimant if loop else None})
    return {"fired": fired, "retired_gateway_crons": retired}


async def sync_loop() -> None:
    logger.info("scheduler cron loop started (interval=%ss, default_orchestrator=%s)",
                SYNC_INTERVAL_SECONDS, DEFAULT_ORCHESTRATOR or "(none; owner agent)")
    while True:
        try:
            await sync()
        except asyncio.CancelledError:
            raise
        except RegistryError as e:
            logger.error("scheduler registry error: %s", e.message)
        except Exception:
            logger.exception("scheduler sync error; continue")
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)
