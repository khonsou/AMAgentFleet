"""P-011 / ADR-0050 local integration check.

Uses the real local PostgreSQL schema and Control Plane state transitions. It does
not start a gateway, call a model, or leave temporary tasks/agents behind.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, patch

from app import db as dbmod
from app import dispatcher
from app import pool as poolmod
from app.execution import enrich_execution
from app.runtime.base import RunResult, RuntimeServiceError


async def main() -> None:
    suffix = uuid.uuid4().hex[:10]
    parent_agent = f"p011-it-parent-{suffix}"
    child_agent = f"p011-it-child-{suffix}"
    agents = [parent_agent, child_agent]
    task_ids: list[int] = []
    parent_thread = str(uuid.uuid4())
    child_thread = str(uuid.uuid4())
    await dbmod.init_db()
    try:
        async with (await dbmod.pool()).acquire() as conn:
            await conn.execute(
                "INSERT INTO fleet_agents(name,status) VALUES ($1,'registered'),($2,'registered')",
                parent_agent, child_agent,
            )
            parent_id = await conn.fetchval(
                """INSERT INTO fleet_tasks(agent_name,input,source,status,pool_status)
                   VALUES ($1,'parent','agent','queued','open') RETURNING id""",
                parent_agent,
            )
            task_ids.append(parent_id)
            child_id = await conn.fetchval(
                """INSERT INTO fleet_tasks(agent_name,input,source,parent_task_id,status,pool_status)
                   VALUES ($1,'review','agent',$2,'queued','open') RETURNING id""",
                child_agent, parent_id,
            )
            task_ids.append(child_id)
            parent_loop_id = await conn.fetchval(
                """INSERT INTO fleet_task_loops(task_id,claimant_type,claimant,thread_id,status,created_by)
                   VALUES ($1,'agent',$2,$3,'claimed','p011-integration') RETURNING id""",
                parent_id, parent_agent, parent_thread,
            )
            child_loop_id = await conn.fetchval(
                """INSERT INTO fleet_task_loops(task_id,claimant_type,claimant,thread_id,status,created_by)
                   VALUES ($1,'agent',$2,$3,'claimed','p011-integration') RETURNING id""",
                child_id, child_agent, child_thread,
            )

        class FakeRuntime:
            def __init__(self):
                self.calls = 0

            async def execute_run(self, *_args, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeServiceError(
                        200,
                        "Artifact delivery incomplete: no produced output artifact was presented",
                    )
                return RunResult(
                    text="final",
                    tokens=1,
                    artifacts=["/mnt/user-data/outputs/final.md"],
                )

        runtime = FakeRuntime()
        with (
            patch("app.dispatcher.get_runtime", return_value=runtime),
            patch("app.dispatcher.audit", new=AsyncMock()),
            patch("app.pool.audit", new=AsyncMock()),
            patch("app.pool.enqueue_loop_run", new=AsyncMock()),
        ):
            await dispatcher._run_loop(parent_loop_id)
            async with (await dbmod.pool()).acquire() as conn:
                waiting_loop = dict(await conn.fetchrow(
                    "SELECT * FROM fleet_task_loops WHERE id=$1", parent_loop_id))
                waiting_task = dict(await conn.fetchrow(
                    "SELECT status,pool_status FROM fleet_tasks WHERE id=$1", parent_id))
            waiting_projection = enrich_execution(dict(waiting_loop))
            assert waiting_loop["waiting_for_children"] is True
            assert waiting_loop["status"] == "delivered"
            assert waiting_task == {"status": "running", "pool_status": "open"}
            assert waiting_projection["execution_status"] == "queued"
            assert waiting_projection["worker_state"] == "waiting_children"
            assert waiting_projection["is_terminal"] is False

            await poolmod.deliver_loop(child_loop_id, "reviewed")
            async with (await dbmod.pool()).acquire() as conn:
                resumed_loop = dict(await conn.fetchrow(
                    "SELECT * FROM fleet_task_loops WHERE id=$1", parent_loop_id))
            assert resumed_loop["status"] == "returned"
            assert resumed_loop["version"] == 2
            assert resumed_loop["thread_id"] == parent_thread
            assert resumed_loop["waiting_for_children"] is False

            await dispatcher._run_loop(parent_loop_id)
            async with (await dbmod.pool()).acquire() as conn:
                final_loop = dict(await conn.fetchrow(
                    "SELECT * FROM fleet_task_loops WHERE id=$1", parent_loop_id))
                final_task = dict(await conn.fetchrow(
                    "SELECT status,pool_status FROM fleet_tasks WHERE id=$1", parent_id))

        assert final_loop["status"] == "delivered"
        assert final_loop["version"] == 2
        assert final_loop["waiting_for_children"] is False
        assert final_loop["error"] is None
        assert final_task == {"status": "success", "pool_status": "review"}
        print(json.dumps({
            "result": "ok",
            "waiting": {
                "loop_status": waiting_loop["status"],
                "task_status": waiting_task,
                "execution_status": waiting_projection["execution_status"],
                "worker_state": waiting_projection["worker_state"],
            },
            "resume": {
                "loop_status": resumed_loop["status"],
                "version": resumed_loop["version"],
                "same_thread": resumed_loop["thread_id"] == parent_thread,
            },
            "final": {
                "loop_status": final_loop["status"],
                "version": final_loop["version"],
                "task_status": final_task,
            },
        }, ensure_ascii=False, indent=2))
    finally:
        async with (await dbmod.pool()).acquire() as conn:
            if task_ids:
                await conn.execute(
                    "DELETE FROM fleet_task_comments WHERE task_id=ANY($1::bigint[])", task_ids)
                await conn.execute(
                    "DELETE FROM fleet_task_loops WHERE task_id=ANY($1::bigint[])", task_ids)
                await conn.execute(
                    "DELETE FROM fleet_tasks WHERE id=ANY($1::bigint[])", task_ids)
            await conn.execute(
                "DELETE FROM fleet_agents WHERE name=ANY($1::text[])", agents)
        if dbmod._pool is not None:
            await dbmod._pool.close()
            dbmod._pool = None


if __name__ == "__main__":
    asyncio.run(main())
