"""Timeline 业务系统 MCP Server（只读优先，P3 首个业务接入）。

数据源：https://timeline.angrymiao.com/<slug>/ （Agent API protocol ≥19.1）
凭据经环境变量注入（TIMELINE_BASE / TIMELINE_BOARD_ID / TIMELINE_PASSWORD），
token 只缓存于本进程内存，绝不进模型上下文（ADR-0005 精神；正式 Token Vault
上线前按"密码当配置管理"处理，AGENTS.md 安全红线 1/5）。

只读工具集：meta 探测 / 看板列表 / 卡片列表 / 单卡详情 / 成员 / 产品目录。
写操作（change-set）按 AGENTS.md 红线 3 需单独评审后上架，本 server 不提供。
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

BASE = os.environ["TIMELINE_BASE"].rstrip("/")  # 例：https://timeline.angrymiao.com/<slug>（真实 slug 不入库）
BOARD_ID = os.environ["TIMELINE_BOARD_ID"]        # 例：cd620e2ebb1739b1
PASSWORD = os.environ["TIMELINE_PASSWORD"]

mcp = FastMCP("timeline")

_token: str | None = None
_token_expires_at: float = 0.0


def _board(path: str) -> str:
    return f"{BASE}/api/boards/{BOARD_ID}{path}"


def _auth() -> str:
    """密码换 token（带进程内缓存与 401 重认证）。密码错会 403/429，直接抛给调用方。"""
    global _token, _token_expires_at
    if _token and time.time() < _token_expires_at - 300:
        return _token
    with httpx.Client(timeout=20) as client:
        r = client.post(_board("/auth"), json={"password": PASSWORD})
        r.raise_for_status()
        data = r.json()
    _token = data["token"]
    exp = data.get("expires_at")
    # expires_at 为 ISO 字符串；解析失败则按 12h 保守缓存
    try:
        from datetime import datetime

        _token_expires_at = datetime.fromisoformat(exp.replace("Z", "+00:00")).timestamp()
    except Exception:
        _token_expires_at = time.time() + 12 * 3600
    return _token


def _get(url: str, *, auth: bool = True, params: dict | None = None) -> Any:
    headers = {"Authorization": f"Bearer {_auth()}"} if auth else {}
    with httpx.Client(timeout=30) as client:
        r = client.get(url, headers=headers, params=params)
        if auth and r.status_code == 401:  # 协议要求：401 → 重新 auth → 重试一次
            global _token
            _token = None
            headers = {"Authorization": f"Bearer {_auth()}"}
            r = client.get(url, headers=headers, params=params)
        if r.status_code == 429:
            retry = r.headers.get("retry_after", "60")
            raise RuntimeError(f"Timeline 限速/锁定中，请 {retry}s 后重试")
        r.raise_for_status()
        return r.json()


@mcp.tool()
def timeline_probe() -> dict:
    """能力探测（免鉴权）：协议版本、capabilities、features、limits、枚举值。
    会话开始时先调本工具，按其返回决定后续行为，不要硬编码限额/枚举。"""
    return _get(f"{BASE}/api/meta", auth=False)


@mcp.tool()
def timeline_list_boards() -> list[dict]:
    """列出本实例可见的看板（board_id/name/version/卡片数）。
    用于校验配置的 TIMELINE_BOARD_ID 是否存在。"""
    return _get(f"{BASE}/api/boards", auth=False).get("boards", [])


@mcp.tool()
def timeline_list_items(
    status: str = "",
    q: str = "",
    date: str = "",
    product_id: str = "",
    member: str = "",
) -> list[dict]:
    """读卡片列表（需鉴权，token 由 server 侧自动管理）。
    过滤参数均可选：status ∈ 待执行/待发布/已发布；q 关键词；date 日期；
    product_id/member 精确过滤。返回精简字段（id/title/type/status/date/product_id）。"""
    params = {k: v for k, v in {
        "status": status, "q": q, "date": date,
        "product_id": product_id, "member": member,
    }.items() if v}
    data = _get(_board("/items"), params=params)
    items = data.get("items", data if isinstance(data, list) else [])
    out = []
    for it in items:
        out.append({
            "id": it.get("id"),
            "title": it.get("title"),
            "type": it.get("type"),
            "status": it.get("status"),
            "date": it.get("date"),
            "product_id": it.get("product_id"),
            "owner": it.get("owner"),
        })
    return out


@mcp.tool()
def timeline_get_item(item_id: str) -> dict:
    """读单卡详情（需鉴权）：完整字段含正文、links、指标、comments。"""
    return _get(_board(f"/items/{item_id}"))


@mcp.tool()
def timeline_list_members() -> list[dict]:
    """读成员目录（需鉴权）。写操作传负责人时必须用这里的 id，禁止传姓名。"""
    data = _get(_board("/members"))
    return data.get("members", data if isinstance(data, list) else [])


@mcp.tool()
def timeline_list_products() -> list[dict]:
    """读产品目录（需鉴权）。"""
    data = _get(_board("/products"))
    return data.get("products", data if isinstance(data, list) else [])


if __name__ == "__main__":
    mcp.run()
