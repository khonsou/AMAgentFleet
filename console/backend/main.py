"""Agent Fleet Console 后端（spike 阶段版）。

数据来源：
- PostgreSQL（DeerFlow 落库：threads_meta / runs / checkpoints）——只读查询
- DeerFlow Gateway（http://127.0.0.1:8001）——消息读取与续跑转发

对应设计文档 §7.8 的极简实现；P2 阶段 Agent Registry 建成后，
fleet 数据源将从 threads_meta 切换为 Registry 的 agents 表。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import asyncpg
import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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

_pool: asyncpg.Pool | None = None


async def pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=5)
    return _pool


def ser(row: asyncpg.Record) -> dict:
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.astimezone(timezone.utc).isoformat()
    return d


@app.get("/api/console/health")
async def health():
    p = await pool()
    async with p.acquire() as conn:
        await conn.fetchval("SELECT 1")
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            gw = (await client.get(f"{GATEWAY}/health")).json()
    except Exception:
        gw = {"status": "unreachable"}
    return {"console": "ok", "gateway": gw}


@app.get("/api/console/overview")
async def overview():
    p = await pool()
    async with p.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
              (SELECT count(*) FROM threads_meta)                                AS threads,
              (SELECT count(*) FROM runs)                                        AS runs,
              (SELECT count(*) FROM runs WHERE status = 'running')               AS running,
              (SELECT count(*) FROM runs WHERE status = 'error')                 AS errored,
              (SELECT coalesce(sum(total_tokens), 0) FROM runs
                WHERE created_at::date = current_date)                           AS tokens_today,
              (SELECT coalesce(sum(total_tokens), 0) FROM runs)                  AS tokens_total,
              (SELECT count(*) FROM checkpoints)                                 AS checkpoints
            """
        )
    return ser(row)


@app.get("/api/console/fleet")
async def fleet():
    """fleet 看板：每行一个 agent 会话（thread）。

    P2 Registry 落地前，以 thread 作为 agent 运行实例的最小观测单元。
    """
    p = await pool()
    async with p.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
              t.thread_id,
              t.display_name,
              t.assistant_id,
              t.status        AS thread_status,
              t.created_at,
              t.updated_at,
              COALESCE(s.run_count, 0)        AS run_count,
              COALESCE(s.total_tokens, 0)     AS total_tokens,
              COALESCE(s.total_input, 0)      AS total_input_tokens,
              COALESCE(s.total_output, 0)     AS total_output_tokens,
              COALESCE(s.llm_calls, 0)        AS llm_call_count,
              s.last_run_status,
              s.last_run_at,
              s.first_message,
              COALESCE(c.checkpoint_count, 0) AS checkpoint_count
            FROM threads_meta t
            LEFT JOIN LATERAL (
              SELECT count(*) AS run_count,
                     sum(r.total_tokens) AS total_tokens,
                     sum(r.total_input_tokens) AS total_input,
                     sum(r.total_output_tokens) AS total_output,
                     sum(r.llm_call_count) AS llm_calls,
                     (SELECT r2.status FROM runs r2
                       WHERE r2.thread_id = t.thread_id
                       ORDER BY r2.created_at DESC LIMIT 1) AS last_run_status,
                     max(r.updated_at) AS last_run_at,
                     (SELECT r3.first_human_message FROM runs r3
                       WHERE r3.thread_id = t.thread_id
                       ORDER BY r3.created_at ASC LIMIT 1) AS first_message
              FROM runs r WHERE r.thread_id = t.thread_id
            ) s ON true
            LEFT JOIN LATERAL (
              SELECT count(*) AS checkpoint_count
              FROM checkpoints c WHERE c.thread_id = t.thread_id
            ) c ON true
            ORDER BY t.updated_at DESC
            """
        )
    return [ser(r) for r in rows]


@app.get("/api/console/threads/{tid}")
async def thread_detail(tid: str):
    p = await pool()
    async with p.acquire() as conn:
        t = await conn.fetchrow("SELECT * FROM threads_meta WHERE thread_id=$1", tid)
        if not t:
            raise HTTPException(404, "thread not found")
        runs = await conn.fetch(
            """
            SELECT run_id, status, model_name, created_at, updated_at,
                   total_input_tokens, total_output_tokens, total_tokens,
                   llm_call_count, message_count, error, stop_reason,
                   first_human_message, last_ai_message
            FROM runs WHERE thread_id=$1 ORDER BY created_at DESC
            """,
            tid,
        )
        cps = await conn.fetchval(
            "SELECT count(*) FROM checkpoints WHERE thread_id=$1", tid
        )
    return {"thread": ser(t), "runs": [ser(r) for r in runs], "checkpoints": cps}


@app.get("/api/console/threads/{tid}/messages")
async def thread_messages(tid: str):
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{GATEWAY}/api/threads/{tid}/messages")
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, resp.text[:500])
    return resp.json()


class MessageIn(BaseModel):
    content: str
    recursion_limit: int = 300


class CreateThreadIn(BaseModel):
    instruction: str | None = None
    assistant_id: str = "lead_agent"
    recursion_limit: int = 300


@app.post("/api/console/threads")
async def create_thread(body: CreateThreadIn):
    """拉起一个新 agent 会话；带 instruction 时直接跑第一轮（阻塞至完成）。"""
    async with httpx.AsyncClient(timeout=600) as client:
        resp = await client.post(f"{GATEWAY}/api/threads", json={})
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, resp.text[:500])
        tid = resp.json()["thread_id"]
        if body.instruction and body.instruction.strip():
            payload = {
                "assistant_id": body.assistant_id,
                "input": {"messages": [{"role": "user", "content": body.instruction.strip()}]},
                "config": {
                    "configurable": {"thread_id": tid},
                    "recursion_limit": body.recursion_limit,
                },
            }
            run = await client.post(f"{GATEWAY}/api/runs/wait", json=payload)
            if run.status_code != 200:
                raise HTTPException(run.status_code, run.text[:500])
    return {"thread_id": tid}


@app.delete("/api/console/threads/{tid}")
async def delete_thread(tid: str):
    """销毁：删除线程及其全部持久化状态（checkpoint / run / 消息）。"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.delete(f"{GATEWAY}/api/threads/{tid}")
    if resp.status_code not in (200, 204):
        raise HTTPException(resp.status_code, resp.text[:500])
    return {"deleted": tid}


@app.post("/api/console/threads/{tid}/messages")
async def send_message(tid: str, msg: MessageIn):
    """续跑：向同一线程投递新消息（runs/wait 阻塞至完成）。

    即 ADR-0001「唤醒 = 重新投递 + 水合断点」的人工触发路径。
    assistant_id 取该线程实际所属的 agent（threads_meta 记录），
    避免 spike 期写死 lead_agent 导致自定义 agent 会话续跑时配置丢失。
    """
    p = await pool()
    async with p.acquire() as conn:
        assistant_id = await conn.fetchval(
            "SELECT assistant_id FROM threads_meta WHERE thread_id=$1", tid
        )
    assistant_id = assistant_id or "lead_agent"
    payload = {
        "assistant_id": assistant_id,
        "input": {"messages": [{"role": "user", "content": msg.content}]},
        "config": {
            "configurable": {"thread_id": tid},
            "recursion_limit": msg.recursion_limit,
        },
    }
    async with httpx.AsyncClient(timeout=600) as client:
        resp = await client.post(f"{GATEWAY}/api/runs/wait", json=payload)
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, resp.text[:500])
    return resp.json()


# ---------------------------------------------------------------------------
# Control Plane 代理（P2：Registry / Dispatcher / Inbox）
# Console 前端只连本后端；以下路由原样转发到 control-plane :8003。
# ---------------------------------------------------------------------------

async def _cp(path: str, method: str = "GET", json_body=None, timeout: float = 60):
    async with httpx.AsyncClient(base_url=CONTROL_PLANE, timeout=timeout) as client:
        resp = await client.request(method, f"/api{path}", json=json_body)
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


@app.get("/api/console/threads/{tid}/artifacts/{path:path}")
async def cp_thread_artifact(tid: str, path: str):
    """产物下载代理（agent 交付物链接走 Console，生产中网关不出 VPC）。"""
    async with httpx.AsyncClient(base_url=GATEWAY, timeout=60) as client:
        resp = await client.get(
            f"/api/threads/{tid}/artifacts/{path}",
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
    async with httpx.AsyncClient(base_url=CONTROL_PLANE, timeout=120) as client:
        resp = await client.request(
            request.method, f"/api/{path}", content=body or None, headers=headers
        )
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers={h: resp.headers[h] for h in _FAP_PASS_RESP_HEADERS if h in resp.headers},
        media_type=resp.headers.get("content-type"),
    )
