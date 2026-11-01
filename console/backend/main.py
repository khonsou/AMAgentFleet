"""Agent Fleet Console 后端：DAO 身份、Control Plane 代理与兼容传输。

Thread／Run 观测和执行统一由 Control Plane 的 RuntimeAdapter 提供；
Console 不直接读取 DeerFlow 数据表，也不包含运行时业务状态逻辑。
"""

from __future__ import annotations

import os

import asyncpg
import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from authjwt import DaoAuthMiddleware
import operations

PG_DSN = os.environ.get(
    "CONSOLE_PG_DSN", "postgresql://postgres@127.0.0.1:5432/deerflow"
)
GATEWAY = os.environ.get("CONSOLE_GATEWAY_URL", "http://127.0.0.1:8001")
CONTROL_PLANE = os.environ.get("CONSOLE_CONTROL_PLANE_URL", "http://127.0.0.1:8003")

app = FastAPI(title="Agent Fleet Console")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(DaoAuthMiddleware)

_pool: asyncpg.Pool | None = None


async def pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=5)
    return _pool


@app.get("/api/console/health")
async def health():
    p = await pool()
    async with p.acquire() as conn:
        await conn.fetchval("SELECT 1")
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
            gw = (await client.get(f"{GATEWAY}/health")).json()
    except Exception:
        gw = {"status": "unreachable"}
    return {"console": "ok", "gateway": gw}


@app.get("/api/console/overview")
async def overview():
    return await _cp("/console/overview")


@app.get("/api/console/fleet")
async def fleet():
    return await _cp("/console/fleet")


@app.get("/api/console/threads/{tid}")
async def thread_detail(tid: str):
    return await _cp(f"/console/threads/{tid}")


@app.get("/api/console/threads/{tid}/messages")
async def thread_messages(tid: str):
    return await _cp(f"/console/threads/{tid}/messages")


class MessageIn(BaseModel):
    content: str
    recursion_limit: int = 300
    assistant_id: str | None = None


class CreateThreadIn(BaseModel):
    instruction: str | None = None
    assistant_id: str = "lead_agent"
    recursion_limit: int = 300


@app.post("/api/console/threads")
async def create_thread(body: CreateThreadIn):
    return await _cp("/console/threads", "POST", body.model_dump(), timeout=600)


@app.delete("/api/console/threads/{tid}")
async def delete_thread(tid: str):
    return await _cp(f"/console/threads/{tid}", "DELETE")


@app.post("/api/console/threads/{tid}/messages")
async def send_message(tid: str, msg: MessageIn):
    return await _cp(f"/console/threads/{tid}/messages", "POST", msg.model_dump(), timeout=600)


# ---------------------------------------------------------------------------
# Control Plane 代理（P2：Registry / Dispatcher / Inbox）
# Console 前端只连本后端；以下路由原样转发到 control-plane :8003。
# ---------------------------------------------------------------------------

async def _cp(path: str, method: str = "GET", json_body=None, timeout: float = 60, idempotency_key: str | None = None):
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
    async with httpx.AsyncClient(trust_env=False, base_url=CONTROL_PLANE, timeout=timeout) as client:
        resp = await client.request(method, f"/api{path}", json=json_body, headers=headers)
    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, resp.text[:500])
    if resp.status_code == 204:
        return None
    return resp.json()


@app.get("/api/console/agents")
async def cp_agents():
    """fleet 看板 Registry 数据源（P2 验收项）：注册 agent 及其组织级元数据。"""
    return await _cp("/agents")


@app.get("/api/console/agents/{name}")
async def cp_agent_detail(name: str):
    return await _cp(f"/agents/{name}")


@app.put("/api/console/agents/{name}")
async def cp_agent_update(name: str, body: dict):
    return await _cp(f"/agents/{name}", "PUT", body)


@app.post("/api/console/agents/{name}/dispatch")
async def cp_agent_dispatch(name: str, body: dict):
    return await _cp(f"/agents/{name}/dispatch", "POST", body)


@app.get("/api/console/agents/{name}/tasks")
async def cp_agent_tasks(name: str):
    return await _cp(f"/tasks?agent={name}")


@app.post("/api/console/agents/{name}/inbox")
async def cp_agent_inbox(name: str, body: dict):
    return await _cp(f"/agents/{name}/inbox", "POST", body)


@app.get("/api/console/agents/{name}/audit")
async def cp_agent_audit(name: str):
    return await _cp(f"/audit?agent={name}")


@app.post("/api/console/agents/{name}/stop")
async def cp_agent_stop(name: str):
    return await _cp(f"/agents/{name}/stop", "POST", {})


@app.post("/api/console/agents/{name}/start")
async def cp_agent_start(name: str):
    return await _cp(f"/agents/{name}/start", "POST", {})


@app.delete("/api/console/agents/{name}", status_code=204)
async def cp_agent_delete(name: str):
    await _cp(f"/agents/{name}", "DELETE")


@app.post("/api/console/agents", status_code=201)
async def cp_agent_create(body: dict):
    return await _cp("/agents", "POST", body)


@app.get("/api/console/mcp-servers")
async def cp_mcp_list():
    return await _cp("/mcp-servers")


@app.post("/api/console/mcp-servers")
async def cp_mcp_create(body: dict):
    return await _cp("/mcp-servers", "POST", body)


@app.put("/api/console/mcp-servers/{name}")
async def cp_mcp_update(name: str, body: dict):
    return await _cp(f"/mcp-servers/{name}", "PUT", body)


@app.delete("/api/console/mcp-servers/{name}", status_code=204)
async def cp_mcp_delete(name: str):
    await _cp(f"/mcp-servers/{name}", "DELETE")


@app.post("/api/console/mcp-servers/render")
async def cp_mcp_render():
    return await _cp("/mcp-servers/render", "POST", {})


@app.get("/api/console/templates")
async def cp_templates():
    return await _cp("/templates")


@app.get("/api/console/tasks")
async def cp_tasks(limit: int = 200):
    """Loop 看板数据源：全量任务（跨 agent），构建协作线程树。"""
    return await _cp(f"/tasks?limit={min(limit, 500)}")


@app.post("/api/console/tasks")
async def cp_task_create(body: dict):
    return await _cp("/tasks", "POST", body)


@app.get("/api/console/tasks/{tid}")
async def cp_task_get(tid: int):
    return await _cp(f"/tasks/{tid}")


@app.post("/api/console/tasks/{tid}/loops")
async def cp_loop_claim(tid: int, body: dict):
    return await _cp(f"/tasks/{tid}/loops", "POST", body)


@app.post("/api/console/tasks/{tid}/accept")
async def cp_task_accept(tid: int, body: dict):
    return await _cp(f"/tasks/{tid}/accept", "POST", body)


@app.post("/api/console/loops/{lid}/return")
async def cp_loop_return(lid: int, body: dict):
    return await _cp(f"/loops/{lid}/return", "POST", body)


@app.post("/api/console/loops/{lid}/deliver")
async def cp_loop_deliver(lid: int, body: dict):
    return await _cp(f"/loops/{lid}/deliver", "POST", body)


@app.post("/api/console/tasks/{tid}/close")
async def cp_task_close(tid: int, author: str = "human"):
    return await _cp(f"/tasks/{tid}/close", "POST", {})


@app.post("/api/console/loops/{lid}/requeue")
async def cp_loop_requeue(lid: int):
    return await _cp(f"/loops/{lid}/requeue", "POST", {})


@app.post("/api/console/loops/{lid}/resume")
async def cp_loop_resume(lid: int):
    return await _cp(f"/loops/{lid}/resume", "POST", {})


@app.get("/api/console/threads/{tid}/artifacts/{path:path}")
async def cp_thread_artifact(tid: str, path: str):
    """产物下载代理（agent 交付物链接走 Console，生产中网关不出 VPC）。"""
    async with httpx.AsyncClient(trust_env=False, base_url=CONTROL_PLANE, timeout=60) as client:
        resp = await client.get(
            f"/api/console/threads/{tid}/artifacts/{path}",
            follow_redirects=True,
        )
    from fastapi import Response
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/octet-stream"),
    )


@app.get("/api/console/threads/{tid}/review-context")
async def cp_review_context(tid: str):
    return await _cp(f"/loops/by-thread/{tid}")


# ---------------------------------------------------------------------------
# FAP PAT 管理代理（ADR-0011 H6：Console 设置页签发/吊销/列表）
# control-plane 侧已限 loopback，此处为本机转发。
# ---------------------------------------------------------------------------

@app.get("/api/console/ops/credential")
async def cp_ops_credential():
    return await _cp("/ops/credential")


@app.post("/api/console/ops/credential/rotate")
async def cp_ops_credential_rotate():
    return await _cp("/ops/credential/rotate", "POST", {})


@app.get("/api/console/pats")
async def cp_pats():
    return await _cp("/pats")


class PatCreateBody(BaseModel):
    name: str
    scopes: list[str]
    expires_in_days: int | None = None


@app.post("/api/console/pats", status_code=201)
async def cp_pat_create(body: PatCreateBody):
    return await _cp("/pats", "POST", body.model_dump())


@app.delete("/api/console/pats/{pat_id}")
async def cp_pat_revoke(pat_id: str):
    return await _cp(f"/pats/{pat_id}", "DELETE", {})


# ---------------------------------------------------------------------------
# 模型 Profile 与 Managed Models（ADR-0028）
# 选模目录经控制面暴露；密钥管理仅存在于已登录 Console → Gateway 的内部链路。
# ---------------------------------------------------------------------------


@app.get("/api/console/models")
async def cp_models():
    return await _cp("/models")


async def _managed_models(method: str, path: str = "", body: dict | None = None):
    async with httpx.AsyncClient(trust_env=False, base_url=GATEWAY, timeout=45) as client:
        resp = await client.request(method, f"/api/managed-models{path}", json=body)
    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, resp.text[:500])
    return resp.json()


@app.get("/api/console/managed-models")
async def console_managed_models():
    return await _managed_models("GET")


@app.put("/api/console/managed-models")
async def console_managed_model_save(body: dict):
    return await _managed_models("PUT", body=body)


@app.post("/api/console/managed-models/test")
async def console_managed_model_test(body: dict):
    return await _managed_models("POST", "/test", body)


@app.get("/api/console/operations/protocol")
async def console_operations_protocol():
    return await operations.protocol_document()


@app.post(operations.GUI_EXECUTE_PATH)
async def console_execute_operation(request: Request, body: operations.GuiOperationBody):
    return await operations.execute_gui_operation(request, body)


# ---------------------------------------------------------------------------
# FAP 同源暴露：非 /api/console/* 的 /api/* 原样转发到 control-plane。
# 第三方 agent 与人用同一入口（GUI 网址即 API 入口），只需网址 + PAT。
# 注册在所有显式路由之后；显式 /api/console/* 优先，不会落入本转发。
# 安全要点：X-Forwarded-For 由本后端重建（取直接对端 IP、忽略客户端自带
# 值），防止伪造 XFF=127.0.0.1 冒充内部流量绕过 PAT 强制。
# ---------------------------------------------------------------------------

_FAP_PASS_HEADERS = ("authorization", "idempotency-key", "content-type")
_FAP_PASS_RESP_HEADERS = ("x-protocol-version", "x-idempotent-replay", "retry-after")


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def fap_proxy(path: str, request: Request) -> Response:
    headers = {h: request.headers[h] for h in _FAP_PASS_HEADERS if h in request.headers}
    headers["x-forwarded-for"] = request.client.host if request.client else "unknown"
    body = await request.body()
    # path:path 不含 query string，必须显式转发，否则 limit/status/agent 等
    # 查询参数在代理层被静默丢弃（下游拿到的是默认值，非调用方意图）。
    url = f"/api/{path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    async with httpx.AsyncClient(trust_env=False, base_url=CONTROL_PLANE, timeout=120) as client:
        resp = await client.request(
            request.method, url, content=body or None, headers=headers
        )
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers={h: resp.headers[h] for h in _FAP_PASS_RESP_HEADERS if h in resp.headers},
        media_type=resp.headers.get("content-type"),
    )
