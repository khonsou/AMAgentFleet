"""人类 GUI 的共享 FAP 执行适配（ADR-0031）。

浏览器只提交 operation ID 与参数；本模块在服务端重新解析协议、校验写确认、
覆盖真实 actor，并沿用 Console 既有受信转发。绝不调用 Runner 专用执行代理。
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

GUI_EXECUTE_PATH = "/api/console/operations/execute"
CONSOLE_SELF = os.environ.get("CONSOLE_SELF_URL", "http://127.0.0.1:8002")
GATEWAY = os.environ.get("CONSOLE_GATEWAY_URL", "http://127.0.0.1:8001")


class GuiOperationBody(BaseModel):
    operation_id: str = Field(min_length=1)
    path_params: dict[str, Any] = Field(default_factory=dict)
    query: dict[str, Any] = Field(default_factory=dict)
    body: dict[str, Any] | None = None
    confirmed: bool = False
    idempotency_key: str | None = None


async def _cp(path: str, method: str = "GET", json_body=None, **kwargs):
    """延迟导入，避免 main 注册路由时形成模块环。"""
    import main
    return await main._cp(path, method, json_body, **kwargs)


async def protocol_document() -> dict[str, Any]:
    return await _cp("/ops/protocol")


def _identity(request: Request | None) -> str:
    user_id = getattr(getattr(request, "state", None), "dao_user_id", None)
    return f"human:{user_id}" if isinstance(user_id, int) else "human"


def _path(operation: dict[str, Any], payload: GuiOperationBody) -> str:
    path = operation["path"]
    if not path.startswith("/api/") or path.startswith("/internal/"):
        raise HTTPException(403, "协议操作路径不受 GUI 执行适配支持")
    for key, value in payload.path_params.items():
        path = path.replace("{" + key + "}", quote(str(value), safe=""))
    if "{" in path or "}" in path:
        raise HTTPException(422, f"路径参数不完整：{path}")
    return path


def _trusted_arguments(operation: dict[str, Any], payload: GuiOperationBody, request: Request | None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    query = dict(payload.query)
    body = dict(payload.body) if payload.body is not None else None
    if operation.get("write"):
        actor = _identity(request)
        spec_body = operation.get("body") or {}
        for key in ("author", "actor", "created_by"):
            if key in spec_body and body is not None:
                body[key] = actor
        for key in ("author", "actor", "created_by"):
            if key in (operation.get("query") or {}):
                query[key] = actor
        if "source" in spec_body and body is not None:
            body["source"] = "web"
    return query, body


async def execute_gui_operation(
    request: Request | None,
    payload: GuiOperationBody,
    *,
    protocol: dict[str, Any] | None = None,
) -> dict[str, Any]:
    protocol = protocol or await protocol_document()
    operation = next((item for item in protocol["operations"] if item["id"] == payload.operation_id), None)
    if operation is None:
        raise HTTPException(404, f"未登记的 Fleet 操作：{payload.operation_id}")
    if operation.get("write") and not payload.confirmed:
        raise HTTPException(409, "写操作必须获得用户明确确认并设置 confirmed=true")
    path = _path(operation, payload)
    query, body = _trusted_arguments(operation, payload, request)
    timeout = min(max(float(operation.get("timeout_seconds", 60)), 5), 600)
    surface = operation.get("surface", "control-plane")
    if surface == "control-plane":
        cp_path = path.removeprefix("/api")
        if query:
            cp_path += "?" + urlencode(query, doseq=True)
        result = await _cp(
            cp_path,
            operation["method"],
            body,
            timeout=timeout,
            idempotency_key=payload.idempotency_key,
        )
        return {"operation_id": payload.operation_id, "status": 200, "result": result, "truncated": False}

    bases = {"console": CONSOLE_SELF, "gateway": GATEWAY}
    if surface not in bases:
        raise HTTPException(422, f"未知协议 surface：{surface}")
    headers: dict[str, str] = {}
    if request and (authorization := request.headers.get("authorization")):
        headers["Authorization"] = authorization
    if payload.idempotency_key:
        headers["Idempotency-Key"] = payload.idempotency_key
    async with httpx.AsyncClient(base_url=bases[surface], timeout=timeout, trust_env=False) as client:
        response = await client.request(operation["method"], path, params=query, json=body, headers=headers)
    text = response.text
    try:
        result = response.json() if text else None
    except ValueError:
        result = text[:30000]
    return {
        "operation_id": payload.operation_id,
        "status": response.status_code,
        "result": result,
        "truncated": len(text) > 30000,
    }
