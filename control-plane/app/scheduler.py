"""Scheduler（池化版，ADR-0010）：cron 触发 = 建池任务，由舰队指挥官认领。

不再使用网关 scheduled-tasks 执行（首次运行时退役 gateway 侧 fleet:* 任务，
避免双触发）。触发判定：croniter 算上一个应触发点 > last_fired 即投递；
每 30s 扫描一轮，进程重启后凭 last_fired 续投（不漏不重）。
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
from .registry import RegistryError, load_all_yaml

logger = logging.getLogger("fleet.scheduler")

SYNC_INTERVAL_SECONDS = 30
COMMANDER = os.environ.get("FLEET_COMMANDER_AGENT", "fleet-cmd-01")
_retired_gateway_crons = False


def _cron_specs(doc: dict) -> list[dict]:
    triggers = doc.get("triggers") or {}
    out = []
    for i, item in enumerate(triggers.get("cron") or []):
        if isinstance(item, str):
            out.append({"cron": item, "timezone": "Asia/Shanghai", "prompt": ""})
        elif isinstance(item, dict):
            out.append({
                "cron": item["cron"],
                "timezone": item.get("timezone", "Asia/Shanghai"),
                "prompt": item.get("prompt", ""),
            })
    return out


async def _retire_gateway_crons() -> int:
    """删除网关侧 fleet:* scheduled tasks（池化前遗留），返回删除数。"""
    global _retired_gateway_crons
    if _retired_gateway_crons:
        return 0
    _retired_gateway_crons = True
    n = 0
    async with httpx.AsyncClient(base_url=GATEWAY, timeout=30) as client:
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
    """扫描一轮：到点的 cron 投递为池任务并由指挥官认领。"""
    from . import pool as poolmod

    retired = await _retire_gateway_crons()
    docs = load_all_yaml()
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    fired: list[int] = []

    for name, doc in docs.items():
        for i, spec in enumerate(_cron_specs(doc)):
            prev = croniter(spec["cron"], now).get_prev(datetime)
            async with (await pool()).acquire() as conn:
                last = await conn.fetchval(
                    "SELECT last_fired_at FROM fleet_cron_fires WHERE agent_name=$1 AND cron_idx=$2",
                    name, i)
            if last is None:
                # 首次见到该 cron：以最近一个已过的触发点为基线，不补投错过的历史
                async with (await pool()).acquire() as conn:
                    await conn.execute(
                        """INSERT INTO fleet_cron_fires (agent_name, cron_idx, last_fired_at)
                           VALUES ($1,$2,$3)
                           ON CONFLICT (agent_name, cron_idx) DO UPDATE SET last_fired_at=EXCLUDED.last_fired_at""",
                        name, i, prev,
                    )
                continue
            if last >= prev:
                continue
            # 到点：建池任务（开放），随后由指挥官认领（途径③）
            prompt = spec["prompt"] or f"[fleet cron] {name} 定时任务 {i}"
            task = await poolmod.create_task(
                prompt, source="cron", created_by="scheduler",
                idempotency_key=f"cron:{name}:{i}:{prev.isoformat()}",
            )
            claimed_by = None
            if COMMANDER:
                try:
                    await poolmod.claim_loop(task["id"], "agent", COMMANDER, created_by="scheduler")
                    claimed_by = COMMANDER
                except poolmod.PoolError as e:
                    logger.warning("cron task %s commander claim failed: %s", task["id"], e.message)
            async with (await pool()).acquire() as conn:
                await conn.execute(
                    """INSERT INTO fleet_cron_fires (agent_name, cron_idx, last_fired_at)
                       VALUES ($1,$2,$3)
                       ON CONFLICT (agent_name, cron_idx) DO UPDATE SET last_fired_at=EXCLUDED.last_fired_at""",
                    name, i, prev,
                )
            fired.append(task["id"])
            await audit("cron.fired", agent_name=name, actor=actor,
                        detail={"task_id": task["id"], "cron": spec["cron"],
                                "occurrence": prev.isoformat(), "claimed_by": claimed_by})
    return {"fired": fired, "retired_gateway_crons": retired}


async def sync_loop() -> None:
    logger.info("scheduler cron loop started (interval=%ss, commander=%s)",
                SYNC_INTERVAL_SECONDS, COMMANDER or "(none)")
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
