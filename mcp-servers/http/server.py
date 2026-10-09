"""通用 HTTP MCP Server——agent 自主对接任意数据网站的平台级能力。

设计定位（与手写 per-system server 相对）：
- 平台只内置这一个通用工具；新数据网站 = 接入文档提示词 + 本工具，零定制；
- 凭据（token 等）由 agent 按文档在会话内获取并放进请求头，不落平台存储；
- 出口管控 = 域名白名单（env ALLOWED_DOMAINS，逗号分隔，注册时配置）；
  方法白名单默认 GET/POST（PUT/PATCH/DELETE 需显式开 ALLOW_WRITE_METHODS=true 并评审）；
- 大响应截断，防止撑爆上下文（红线：上下文纪律）。
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlparse

import httpx
from mcp.server.fastmcp import FastMCP

ALLOWED_DOMAINS = {
    d.strip().lower()
    for d in os.environ.get("ALLOWED_DOMAINS", "").split(",")
    if d.strip()
}
ALLOW_WRITE = os.environ.get("ALLOW_WRITE_METHODS", "false").lower() == "true"
ALLOWED_METHODS = {"GET", "POST"} | ({"PUT", "PATCH", "DELETE"} if ALLOW_WRITE else set())
MAX_BODY_CHARS = int(os.environ.get("MAX_BODY_CHARS", "12000"))

mcp = FastMCP("http")


def _check(url: str, method: str) -> None:
    if not ALLOWED_DOMAINS:
        raise PermissionError("ALLOWED_DOMAINS 未配置：该 http 工具未放行任何域名，请在 MCP 注册时配置")
    host = (urlparse(url).hostname or "").lower()
    if host not in ALLOWED_DOMAINS:
        raise PermissionError(f"域名未白名单：{host}（允许：{sorted(ALLOWED_DOMAINS)}）")
    if method.upper() not in ALLOWED_METHODS:
        raise PermissionError(f"方法未放行：{method}（允许：{sorted(ALLOWED_METHODS)}）")


@mcp.tool()
def http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout_seconds: int = 30,
) -> str:
    """向白名单域名发起 HTTP 请求并返回截断后的响应体（含状态码）。

    用法：按你手中的接入文档操作——探测端点、鉴权换取 token、带 Bearer 读数据。
    凭据只在本次调用内使用；遇到 401 按文档重新鉴权后重试一次。
    url 必须是注册时白名单的域名；method 默认仅 GET/POST。
    body 为 JSON 对象（POST/PUT/PATCH 用）。响应超 12k 字符会截断并标注。"""
    method = method.upper()
    _check(url, method)
    kwargs: dict = {
        "url": url,
        "method": method,
        "headers": headers or {},
        "timeout": min(max(timeout_seconds, 5), 120),
    }
    if body is not None:
        kwargs["json"] = body
    try:
        with httpx.Client(follow_redirects=True) as client:
            r = client.request(**kwargs)
        text = r.text
        truncated = len(text) > MAX_BODY_CHARS
        if truncated:
            text = text[:MAX_BODY_CHARS]
        out = {"status": r.status_code, "headers": dict(r.headers), "body": text}
        if truncated:
            out["truncated"] = f"响应已截断（原 {len(r.text)} 字符，上限 {MAX_BODY_CHARS}）"
        if r.status_code == 401:
            out["hint"] = "401：按接入文档重新鉴权后重试一次"
        return json.dumps(out, ensure_ascii=False)
    except httpx.HTTPError as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
