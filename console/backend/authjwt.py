"""DAO（angrymiao-auth）员工 OAuth JWT 校验（P4 人身份路径）。

接入规格参照 timeline 项目 docs/oauth-auth-integration.md（同一 OAuth 平台）：
  - token 为 RS256 JWT，RSA 公钥 PEM 本地验签 + issuer 比对，不做 introspection 回源；
  - claims 约定与 coin backend 一致：user_id(int) / role / exp / iss；
  - 前端经 PKCE 整页跳转取得 access token（纯内存），调用 Console API 时带
    Authorization: Bearer <JWT>；本中间件保护 /api/console/*（FAP /api/* 仍走 PAT，
    两套凭据并存：人=DAO JWT，第三方 agent=PAT）。

开关与环境（compose / .env 注入）：
  FLEET_AUTH_ENABLED=1            启用；缺省 0（开发态现状不受影响）
  FLEET_AUTH_JWT_PUBLIC_KEY_PEM   RSA 公钥 PEM 文本（与 _PATH 二选一）
  FLEET_AUTH_JWT_PUBLIC_KEY_PATH  RSA 公钥 PEM 文件路径
  FLEET_AUTH_JWT_ISSUER          issuer 值（与 DAO 配置一致；缺省不校验 iss）

临时豁免（过渡项，P4 后续收口）：
  - /api/console/health          容器健康检查
  - /api/console/threads/*/artifacts/*  产物下载是 <a download> 直链，浏览器不带
    Authorization 头；改为 fetch+blob 后可纳入保护
"""

from __future__ import annotations

import os

import jwt
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

_ENABLED = os.environ.get("FLEET_AUTH_ENABLED", "0") == "1"
_ISSUER = os.environ.get("FLEET_AUTH_JWT_ISSUER", "")


def _load_public_key() -> str | None:
    pem = os.environ.get("FLEET_AUTH_JWT_PUBLIC_KEY_PEM", "").strip()
    if pem:
        return pem
    path = os.environ.get("FLEET_AUTH_JWT_PUBLIC_KEY_PATH", "").strip()
    if path and os.path.exists(path):
        return open(path, encoding="utf-8").read()
    return None


_PUBLIC_KEY = _load_public_key() if _ENABLED else None


def _exempt(path: str) -> bool:
    if path == "/api/console/health":
        return True
    if path.startswith("/api/console/threads/") and "/artifacts/" in path:
        return True
    return False


class DaoAuthMiddleware(BaseHTTPMiddleware):
    """/api/console/* 的 DAO JWT 强制校验；其余路径（含 FAP 转发）原样放行。"""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not _ENABLED or not path.startswith("/api/console/") or _exempt(path):
            return await call_next(request)
        if _PUBLIC_KEY is None:
            return JSONResponse(
                {"detail": "认证未配置：缺少 FLEET_AUTH_JWT_PUBLIC_KEY_PEM/PATH"},
                status_code=500,
            )
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return JSONResponse({"detail": "未登录（缺少 DAO 访问令牌）"}, status_code=401)
        token = header[7:].strip()
        try:
            kwargs: dict = {"algorithms": ["RS256"]}
            if _ISSUER:
                kwargs["issuer"] = _ISSUER
            claims = jwt.decode(token, _PUBLIC_KEY, **kwargs)
        except jwt.PyJWTError:
            return JSONResponse({"detail": "令牌无效或已过期，请重新登录"}, status_code=401)
        uid = claims.get("user_id")
        if not isinstance(uid, int) or uid <= 0:
            return JSONResponse({"detail": "令牌缺少有效 user_id"}, status_code=401)
        # 身份写入请求状态，路由内可读（验收人真实身份、审计归因的挂钩点）
        request.state.dao_user_id = uid
        request.state.dao_role = claims.get("role", "")
        return await call_next(request)
