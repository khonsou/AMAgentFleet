"""FAP v1（ADR-0011）：第三方 agent 接入面——自发现 / 协议头 / 机器可读文档。

- `GET /api/meta`（免鉴权）：protocol_version / server_version / capabilities / doc
- 全响应 `X-Protocol-Version` 头（breaking 变更升 major）
- `GET /api/agent-doc`：面向 LLM 的 markdown 全文

capabilities 动态口径（QA-5）：能力实际启用后才出现在 meta 里。
本模块只放纯只读的自发现面；幂等/限速/PAT 中间件见 fap_middleware.py。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .fap_doc import FAP_AGENT_DOC_MD

PROTOCOL_VERSION = "1.0"

router = APIRouter(tags=["fap"])

# 能力开关（QA-5 动态 capabilities）：任务 2/5 完成后翻 True。
_FEATURE_FLAGS: dict[str, bool] = {
    "idempotency": False,   # 任务 2（fap_idempotency_keys 上线后置 True）
    "pat_auth": False,      # 任务 5（PAT 中间件上线后置 True）
}

# 基础能力：v0.1.0 已落地（agent-api.md §2 A/B/C/E 域 + D 域网关原生）。
_BASE_CAPABILITIES = [
    "tasks",
    "loops",
    "threads",           # D 域：网关原生执行面（meta 只宣告实例具备该能力）
    "agents_registry",
    "scheduled_tasks",
    "feedback",
    "fleet_operations",  # ADR-0017：全量操作协议与默认操作 Agent
]

_FEATURE_CAPABILITIES = {
    "idempotency": "idempotency",
    "pat_auth": "pat_auth",
}


def set_feature_flag(name: str, enabled: bool) -> None:
    """任务 2/5 落地时由对应模块调用，同步 meta capabilities。"""
    if name not in _FEATURE_FLAGS:
        raise KeyError(f"unknown FAP feature flag: {name}")
    _FEATURE_FLAGS[name] = enabled


def current_capabilities() -> list[str]:
    caps = list(_BASE_CAPABILITIES)
    for flag, cap in _FEATURE_CAPABILITIES.items():
        if _FEATURE_FLAGS[flag]:
            caps.append(cap)
    return caps


@router.get("/api/meta")
async def meta(request: Request) -> JSONResponse:
    """免鉴权自发现（H1）。server_version 口径 = 控制面 FastAPI version（QA-6）。"""
    app = request.app
    return JSONResponse(
        {
            "protocol_version": PROTOCOL_VERSION,
            "server_version": app.version,
            "capabilities": current_capabilities(),
            "doc": "/api/agent-doc",
            "operations_protocol": "/api/ops/protocol",
        }
    )


@router.get("/api/agent-doc")
async def agent_doc() -> PlainTextResponse:
    """机器可读文档全文（H2），随 PROTOCOL_VERSION 同发。"""
    return PlainTextResponse(FAP_AGENT_DOC_MD, media_type="text/markdown; charset=utf-8")


async def protocol_header_middleware(request: Request, call_next):
    """全响应带 X-Protocol-Version（H3）。"""
    response = await call_next(request)
    response.headers["X-Protocol-Version"] = PROTOCOL_VERSION
    return response
