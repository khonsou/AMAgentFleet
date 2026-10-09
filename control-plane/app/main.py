"""Admin API：Registry / 任务派发 / Inbox / 审计 / Scheduler 对账（FastAPI :8003）。

所有写操作以单服务身份执行（单一共享组织作用域，spike 0002）；
人经 Console 调用，actor 字段透传操作人标识（审计仍记操作人，§1-9）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from . import inbox, mcpservers, registry, scheduler, templates
from . import pool as poolmod
from .db import GATEWAY, audit, init_db, pool

app = FastAPI(title="Agent Fleet Control Plane", version="0.2.0")
_state: dict[str, asyncio.Task] = {}


@app.on_event("startup")
async def startup() -> None:
    await init_db()
    # dispatcher consumer 懒启动（首次领取时拉起，见 dispatcher.enqueue_loop_run）
    _state["scheduler"] = asyncio.create_task(scheduler.sync_loop())
    _state["inbox"] = asyncio.create_task(inbox.flush_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    for t in _state.values():
        t.cancel()


def _reg_err(exc: registry.RegistryError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


# ---------------------------------------------------------------- Registry

@app.get("/api/agents")
async def list_agents() -> list[dict[str, Any]]:
    return await registry.list_registry()


@app.get("/api/agents/{name}")
async def get_agent(name: str) -> dict[str, Any]:
    try:
        doc = registry.load_yaml_doc(name)
    except registry.RegistryError as exc:
        raise _reg_err(exc)
    async with (await pool()).acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM fleet_agents WHERE name=$1", name)
    return {"registry": dict(row) if row else None, "yaml": doc}


class AgentUpsertBody(BaseModel):
    yaml: dict[str, Any]


@app.post("/api/agents", status_code=201)
async def create_agent(body: AgentUpsertBody, actor: str = "admin") -> dict[str, Any]:
    name = (body.yaml.get("metadata") or {}).get("name")
    if not name:
        raise HTTPException(422, "metadata.name required")
    try:
        row = await registry.apply_agent(name, body.yaml)
    except registry.RegistryError as exc:
        raise _reg_err(exc)
    await audit("agent.deployed", agent_name=name, actor=actor)
    await mcpservers.render_and_reload(actor=actor)  # egress 声明变化 → 重渲染出口白名单
    return row


@app.put("/api/agents/{name}")
async def update_agent(name: str, body: AgentUpsertBody, actor: str = "admin") -> dict[str, Any]:
    if (body.yaml.get("metadata") or {}).get("name") not in (None, name):
        raise HTTPException(422, "metadata.name mismatch")
    body.yaml.setdefault("metadata", {})["name"] = name
    try:
        row = await registry.apply_agent(name, body.yaml)
    except registry.RegistryError as exc:
        raise _reg_err(exc)
    await audit("agent.config_updated", agent_name=name, actor=actor)
    await mcpservers.render_and_reload(actor=actor)  # egress 声明变化 → 重渲染出口白名单
    return row


@app.delete("/api/agents/{name}", status_code=204)
async def delete_agent(name: str, actor: str = "admin") -> None:
    try:
        await registry.remove_agent(name)
    except registry.RegistryError as exc:
        raise _reg_err(exc)
    await audit("agent.destroyed", agent_name=name, actor=actor)


# ---------------------------------------------------------------- 生命周期控制

async def _set_agent_status(name: str, status: str) -> None:
    async with (await pool()).acquire() as conn:
        await conn.execute(
            "UPDATE fleet_agents SET status=$2, updated_at=now() WHERE name=$1", name, status)


async def _cron_tasks_of(name: str) -> list[dict]:
    async with httpx.AsyncClient(base_url=GATEWAY, timeout=30) as client:
        tasks = (await client.get("/api/scheduled-tasks")).json()
    return [t for t in tasks
            if t.get("assistant_id") == name and t.get("title", "").startswith("fleet:")]


@app.post("/api/agents/{name}/stop")
async def stop_agent(name: str, actor: str = "admin") -> dict[str, Any]:
    """停止 = 暂停该 agent 的全部 fleet cron + 状态置 hibernated。

    之后新的 dispatch/inbox 唤醒会被拒绝（dispatcher 校验状态），
    已跑任务不受影响；配置可继续修改，start 后恢复。
    """
    paused = []
    for t in await _cron_tasks_of(name):
        async with httpx.AsyncClient(base_url=GATEWAY, timeout=30) as client:
            r = await client.post(f"/api/scheduled-tasks/{t['id']}/pause")
        if r.status_code == 200:
            paused.append(t["title"])
    await _set_agent_status(name, "hibernated")
    await audit("agent.stopped", agent_name=name, actor=actor, detail={"paused_crons": paused})
    return {"status": "hibernated", "paused_crons": paused}


@app.post("/api/agents/{name}/start")
async def start_agent(name: str, actor: str = "admin") -> dict[str, Any]:
    """拉起 = 恢复 fleet cron + 状态回 registered（幂等，配置为最新已部署版本）。

    配置已销毁（YAML 不存在）时拒绝：拉起不能凭空造出配置，必须重新部署 YAML。"""
    try:
        registry.load_yaml_doc(name)
    except registry.RegistryError as exc:
        raise _reg_err(exc)
    resumed = []
    for t in await _cron_tasks_of(name):
        async with httpx.AsyncClient(base_url=GATEWAY, timeout=30) as client:
            r = await client.post(f"/api/scheduled-tasks/{t['id']}/resume")
        if r.status_code == 200:
            resumed.append(t["title"])
    await _set_agent_status(name, "registered")
    await audit("agent.started", agent_name=name, actor=actor, detail={"resumed_crons": resumed})
    return {"status": "registered", "resumed_crons": resumed}


# ---------------------------------------------------------------- Tasks

class DispatchBody(BaseModel):
    input: str = Field(min_length=1)
    source: str = Field(default="web", pattern="^(web|im|agent|admin)$")
    thread_id: str | None = None
    parent_task_id: int | None = None
    idempotency_key: str | None = None
    actor: str = "admin"


@app.post("/api/agents/{name}/dispatch", status_code=202)
async def dispatch(name: str, body: DispatchBody) -> dict[str, int]:
    """兼容层：派发 = 建 task(open) + 指派 loop 给该 agent（ADR-0010 途径①）。"""
    async with (await pool()).acquire() as conn:
        row = await conn.fetchrow("SELECT status FROM fleet_agents WHERE name=$1", name)
    if row is None:
        raise HTTPException(404, f"agent not registered: {name}")
    if row["status"] not in ("registered", "running"):
        raise HTTPException(409, f"agent '{name}' is {row['status']}; start it before dispatching")
    task = await poolmod.create_task(body.input, source=body.source, assignee=name,
                                     created_by=body.actor, parent_task_id=body.parent_task_id,
                                     idempotency_key=body.idempotency_key)
    return {"task_id": task["id"]}


@app.get("/api/tasks")
async def list_tasks(
    agent: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, le=200),
) -> list[dict[str, Any]]:
    clause, args = "", []
    if agent:
        args.append(agent); clause = "WHERE agent_name=$1"
    if status:
        args.append(status)
        clause += (" AND " if clause else "WHERE ") + f"status=${len(args)}"
    args.append(limit)
    async with (await pool()).acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM fleet_tasks {clause} ORDER BY id DESC LIMIT ${len(args)}", *args)
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- Inbox

class InboxBody(BaseModel):
    payload: str = Field(min_length=1)
    source: str = "agent"


@app.post("/api/agents/{name}/inbox", status_code=202)
async def inbox_post(name: str, body: InboxBody) -> dict[str, int]:
    """入 Inbox 即返回；攒批合并由后台 flush_loop 在 5s 内完成投递。"""
    inbox_id = await inbox.post(name, body.payload, source=body.source)
    return {"inbox_id": inbox_id}


@app.get("/api/agents/{name}/inbox")
async def inbox_list(name: str, limit: int = Query(default=50, le=200)) -> list[dict[str, Any]]:
    return await inbox.list_messages(name, limit)


# ---------------------------------------------------------------- Audit / Scheduler

@app.get("/api/audit")
async def audit_list(
    agent: str | None = None, limit: int = Query(default=100, le=500),
) -> list[dict[str, Any]]:
    async with (await pool()).acquire() as conn:
        if agent:
            rows = await conn.fetch(
                "SELECT * FROM audit_events WHERE agent_name=$1 ORDER BY id DESC LIMIT $2",
                agent, limit)
        else:
            rows = await conn.fetch(
                "SELECT * FROM audit_events ORDER BY id DESC LIMIT $1", limit)
    return [dict(r) for r in rows]


@app.post("/api/scheduler/sync")
async def scheduler_sync() -> dict[str, Any]:
    try:
        return await scheduler.sync(actor="admin")
    except registry.RegistryError as exc:
        raise _reg_err(exc)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------- MCP Servers

def _mcp_err(exc: mcpservers.McpError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


@app.get("/api/mcp-servers")
async def mcp_list() -> list[dict[str, Any]]:
    return await mcpservers.list_servers()


class McpServerBody(BaseModel):
    name: str = Field(min_length=1)
    command: str = Field(min_length=1)
    args: list[str] = []
    env: dict[str, str] = {}
    secrets: dict[str, str] = {}
    description: str = ""
    enabled: bool = True
    actor: str = "admin"


@app.post("/api/mcp-servers", status_code=201)
async def mcp_create(body: McpServerBody) -> dict[str, Any]:
    try:
        result = await mcpservers.upsert_server(
            body.name, command=body.command, args=body.args, env=body.env,
            secrets=body.secrets, description=body.description,
            enabled=body.enabled, actor=body.actor,
        )
        result["render"] = await mcpservers.render_and_reload(actor=body.actor)
        return result
    except mcpservers.McpError as exc:
        raise _mcp_err(exc)


@app.put("/api/mcp-servers/{name}")
async def mcp_update(name: str, body: McpServerBody) -> dict[str, Any]:
    try:
        result = await mcpservers.upsert_server(
            name, command=body.command, args=body.args, env=body.env,
            secrets=body.secrets or None, description=body.description,
            enabled=body.enabled, actor=body.actor,
        )
        result["render"] = await mcpservers.render_and_reload(actor=body.actor)
        return result
    except mcpservers.McpError as exc:
        raise _mcp_err(exc)


@app.delete("/api/mcp-servers/{name}", status_code=204)
async def mcp_delete(name: str, actor: str = "admin") -> None:
    try:
        await mcpservers.delete_server(name, actor=actor)
    except mcpservers.McpError as exc:
        raise _mcp_err(exc)
    await mcpservers.render_and_reload(actor=actor)


@app.post("/api/mcp-servers/render")
async def mcp_render(actor: str = "admin") -> dict[str, Any]:
    """全量重渲染 + 网关 cache reset（secrets 轮换后调用）。"""
    return await mcpservers.render_and_reload(actor=actor)


# ---------------------------------------------------------------- Templates

@app.get("/api/templates")
async def template_list() -> list[dict[str, Any]]:
    return templates.list_templates()


class TemplateRenderBody(BaseModel):
    templateId: str
    name: str = Field(min_length=1)
    displayName: str = ""
    owner: str = ""
    costCenter: str = ""


@app.post("/api/templates/render", status_code=201)
async def template_render(body: TemplateRenderBody, actor: str = "admin") -> dict[str, Any]:
    """模板 → agent 定义文档（不落盘）；创建仍需走 POST /api/agents。"""
    try:
        doc = templates.render_template(body.templateId, body.model_dump())
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    return {"yaml": doc}


# ---------------------------------------------------------------- 任务池（ADR-0010）

def _pool_err(exc: poolmod.PoolError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


class TaskCreateBody(BaseModel):
    input: str = Field(min_length=1)
    source: str = Field(default="web", pattern="^(web|im|agent|inbox|admin|cron)$")
    assignee: str | None = None        # 途径①：人页面指派给 agent
    created_by: str = "human"
    parent_task_id: int | None = None  # 指挥官分解 sub-task
    caller_thread_id: str | None = None  # runner 工具反解父 task 用


@app.post("/api/tasks", status_code=201)
async def task_create(body: TaskCreateBody) -> dict[str, Any]:
    """建 task（open）。assignee=agent 名时同时建 loop 并入队。"""
    if body.assignee:
        async with (await pool()).acquire() as conn:
            agent = await conn.fetchval("SELECT status FROM fleet_agents WHERE name=$1", body.assignee)
        if agent is None:
            raise HTTPException(404, f"agent not registered: {body.assignee}")
        if agent not in ("registered", "running"):
            raise HTTPException(409, f"agent '{body.assignee}' is {agent}; start it first")
    parent = body.parent_task_id
    if parent is None and body.caller_thread_id:
        async with (await pool()).acquire() as conn:
            parent = await conn.fetchval(
                "SELECT task_id FROM fleet_task_loops WHERE thread_id=$1 ORDER BY id DESC LIMIT 1",
                body.caller_thread_id)
    t = await poolmod.create_task(body.input, source=body.source, assignee=body.assignee,
                               created_by=body.created_by, parent_task_id=parent)
    return {"task_id": t["id"], "pool_status": t["pool_status"]}


class ClaimBody(BaseModel):
    claimant_type: str = Field(pattern="^(agent|human)$")
    claimant: str = Field(min_length=1)
    created_by: str = "human"


@app.post("/api/tasks/{task_id}/loops", status_code=201)
async def loop_claim(task_id: int, body: ClaimBody) -> dict[str, Any]:
    """途径②③：领取（竞争非独占，同 task ≤3）。agent 领取即入队执行。"""
    try:
        loop = await poolmod.claim_loop(task_id, body.claimant_type, body.claimant, created_by=body.created_by)
    except poolmod.PoolError as exc:
        raise _pool_err(exc)
    return {"loop_id": loop["id"], "status": loop["status"]}


class AcceptBody(BaseModel):
    loop_id: int
    author: str = "human"


@app.post("/api/tasks/{task_id}/accept")
async def task_accept(task_id: int, body: AcceptBody) -> dict[str, str]:
    """强制验收：采纳某个 loop 的交付 → task done；其余 loop 归档。"""
    try:
        await poolmod.accept_loop(task_id, body.loop_id, author=body.author)
    except poolmod.PoolError as exc:
        raise _pool_err(exc)
    return {"pool_status": "done"}


class ReturnBody(BaseModel):
    comment: str = Field(min_length=1)
    author: str = "human"


@app.post("/api/loops/{loop_id}/return", status_code=202)
async def loop_return(loop_id: int, body: ReturnBody) -> dict[str, str]:
    """打回 loop（不动 task）：意见落评论，同 thread 自动续跑修正。"""
    try:
        await poolmod.return_loop(loop_id, body.comment, author=body.author)
    except poolmod.PoolError as exc:
        raise _pool_err(exc)
    return {"status": "returned"}


class DeliverBody(BaseModel):
    content: str = Field(min_length=1)
    author: str = "human"


@app.post("/api/loops/{loop_id}/deliver")
async def loop_deliver(loop_id: int, body: DeliverBody) -> dict[str, str]:
    """人交付（人机同构）：内容与 agent 产出同构入 deliverable。"""
    await poolmod.human_deliver(loop_id, body.content, author=body.author)
    return {"status": "delivered"}


class CommentBody(BaseModel):
    author: str = "human"
    content: str = Field(min_length=1)
    loop_id: int | None = None


@app.post("/api/tasks/{task_id}/comments", status_code=201)
async def task_comment(task_id: int, body: CommentBody) -> dict[str, str]:
    await poolmod.add_comment(task_id, author=body.author, content=body.content, loop_id=body.loop_id)
    return {"status": "ok"}


@app.get("/api/tasks/{task_id}")
async def task_get(task_id: int) -> dict[str, Any]:
    d = await poolmod.task_detail(task_id)
    if d is None:
        raise HTTPException(404, f"task {task_id} not found")
    return d


@app.get("/api/review-queue")
async def tasks_pending_review() -> list[dict[str, Any]]:
    return await poolmod.pending_review()


@app.post("/api/tasks/{task_id}/close")
async def task_close(task_id: int, author: str = "human") -> dict[str, str]:
    """关闭作废：task 终结（非采纳路径），所有 loop 归档。"""
    async with (await pool()).acquire() as conn:
        ok = await conn.fetchval(
            "UPDATE fleet_tasks SET pool_status='done', status='rejected', finished_at=now(), updated_at=now() WHERE id=$1 AND pool_status != 'done' RETURNING id",
            task_id)
        if ok is None:
            raise HTTPException(409, f"task {task_id} 已终结")
        await conn.execute(
            "UPDATE fleet_task_loops SET status='archived', updated_at=now() WHERE task_id=$1 AND status NOT IN ('accepted','archived')",
            task_id)
    await audit("task.closed", actor=author, detail={"task_id": task_id})
    return {"pool_status": "done"}


@app.post("/api/loops/{loop_id}/requeue", status_code=202)
async def loop_requeue(loop_id: int) -> dict[str, str]:
    """重派：error/滞留的 agent loop 回 claimed 并入队（human loop 不适用）。"""
    async with (await pool()).acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE fleet_task_loops SET status='claimed', error=NULL, updated_at=now()
               WHERE id=$1 AND claimant_type='agent' AND status IN ('claimed','returned')
               RETURNING id""", loop_id)
    if row is None:
        raise HTTPException(409, "loop 不存在、非 agent 领取或状态不可重派")
    from .dispatcher import enqueue_loop_run
    await enqueue_loop_run(loop_id)
    return {"status": "claimed"}


@app.get("/api/loops/by-thread/{thread_id}")
async def loop_by_thread(thread_id: str) -> dict[str, Any]:
    """会话详情页的验收上下文：thread → 所属 loop + task 池状态。"""
    async with (await pool()).acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM fleet_task_loops WHERE thread_id=$1 ORDER BY id DESC LIMIT 1", thread_id)
        if row is None:
            return {"loop": None}
        task = await conn.fetchrow("SELECT id, pool_status FROM fleet_tasks WHERE id=$1", row["task_id"])
    return {"loop": dict(row), "task": dict(task)}
