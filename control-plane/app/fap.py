"""FAP v1（ADR-0011）：第三方 agent 接入面——自发现 / 协议头 / 机器可读文档。

- `GET /api/meta`（免鉴权）：protocol_version / server_version / capabilities / doc
- 全响应 `X-Protocol-Version` 头（breaking 变更升 major）
- `GET /api/agent-doc`：面向 LLM 的 markdown 全文

capabilities 动态口径（QA-5）：能力实际启用后才出现在 meta 里。
本模块只放纯只读的自发现面；幂等/限速/PAT 中间件见 fap_middleware.py。
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from . import fap_middleware
from .fap_doc import FAP_AGENT_DOC_MD
from .fleet_ops import load_operations_protocol
from .runtime import get_runtime

PROTOCOL_VERSION = "1.0"

router = APIRouter(tags=["fap"])

# 能力开关（QA-5 动态 capabilities）：任务 2/5 完成后翻 True。
_FEATURE_FLAGS: dict[str, bool] = {
    "idempotency": False,   # 任务 2（fap_idempotency_keys 上线后置 True）
    "pat_auth": False,      # 任务 5（PAT 中间件上线后置 True）
}

# 基础能力：Fleet 控制面与稳定 Runtime surface。
_BASE_CAPABILITIES = [
    "tasks",
    "loops",
    "threads",           # D 域：Control Plane 稳定 Runtime 执行面
    "agents_registry",
    "scheduled_tasks",
    "feedback",
    "fleet_operations",  # ADR-0017：全量操作协议与默认操作 Agent
    "runtime_adapter",   # ADR-0032：Runtime 能力自发现
    "batch_tasks",       # ADR-0038：一次读取最多 100 个 Task
    "failure_semantics", # ADR-0038：结构化失败与重试状态
    "platform_evaluation",
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


def _public_operations(app) -> list[dict[str, Any]]:
    """只发布当前进程实际注册且允许公网 PAT 访问的 Control Plane 路由。

    新版 FastAPI 可能保留嵌套 Router；OpenAPI 展开这些路由，顶层列表补充
    /openapi.json 等不出现在 schema 中的基础设施端点。
    """
    actual = {
        (method, path.replace(":path}", "}"))
        for route in app.routes
        if (path := getattr(route, "path", None)) is not None
        for method in getattr(route, "methods", set())
    }
    for path, operations in app.openapi()["paths"].items():
        for method in operations:
            if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                actual.add((method.upper(), path))
    return [
        {
            "id": operation["id"],
            "method": operation["method"],
            "path": operation["path"],
            "required_scope": (
                None if operation["path"] in ("/api/meta", "/api/agent-doc")
                else "write" if operation.get("write") else "read"
            ),
        }
        for operation in load_operations_protocol()["operations"]
        if operation["surface"] == "control-plane"
        and (operation["method"], operation["path"]) in actual
    ]


@router.get("/api/meta")
async def meta(request: Request) -> JSONResponse:
    """免鉴权自发现；版本、权限与公开路由均对应当前部署。"""
    app = request.app
    capabilities = current_capabilities()
    if get_runtime().capabilities().cancel:
        capabilities.append("runtime_cancel")
    read_methods = ["GET", "HEAD", "OPTIONS"]
    write_methods = ["POST", "PUT", "PATCH", "DELETE"]
    return JSONResponse(
        {
            "protocol_version": PROTOCOL_VERSION,
            "api_version": app.version,
            "server_version": app.version,
            "deployment_version": os.environ.get("FLEET_DEPLOY_VERSION", app.version),
            "capabilities": capabilities,
            "authentication": {
                "type": "bearer_pat",
                "write_implies_read": True,
                "method_scope_matrix": {
                    **{method: "read" for method in read_methods},
                    **{method: "write" for method in write_methods},
                },
                "scopes": {
                    "read": read_methods,
                    "write": read_methods + write_methods,
                },
            },
            "rate_limit": {
                "requests_per_minute": fap_middleware.RATE_LIMIT_PER_MINUTE,
                "retry_after_header": "Retry-After",
            },
            "public_operations": _public_operations(app),
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
