"""DAO（angrymiao-auth）员工 OAuth JWT 校验（P4 人身份路径）。

接入规格参照 timeline 项目 docs/oauth-auth-integration.md（同一 OAuth 平台）：
  - RS256 公钥本地验签，校验 issuer/audience，仅接受 Fleet access token；
  - claims 约定与 coin backend 一致：user_id(int) / role / exp / iss；
  - 前端经 PKCE 整页跳转取得 access token（纯内存），调用 Console API 时带
    Authorization: Bearer <JWT>；本中间件保护 /api/console/*（FAP /api/* 仍走 PAT，
    两套凭据并存：人=DAO JWT，第三方 agent=PAT）。

开关与环境（compose / .env 注入）：
  FLEET_AUTH_ENABLED=1            启用；缺省 0（开发态现状不受影响）
  FLEET_AUTH_JWT_PUBLIC_KEY_PEM   RSA 公钥 PEM 文本（与 _PATH 二选一）
  FLEET_AUTH_JWT_PUBLIC_KEY_PATH  RSA 公钥 PEM 文件路径
  FLEET_AUTH_JWT_ISSUER          必填；与 DAO 配置一致

豁免：
  - /api/console/health          容器健康检查
"""

from __future__ import annotations

import os

import jwt
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

_ENABLED = os.environ.get("FLEET_AUTH_ENABLED", "0") == "1"
_ISSUER = os.environ.get("FLEET_AUTH_JWT_ISSUER", "")
_CLIENT_ID = "am-agent-fleet"


def _load_public_key() -> str | None:
    pem = os.environ.get("FLEET_AUTH_JWT_PUBLIC_KEY_PEM", "").strip()
    if pem:
        return pem
    path = os.environ.get("FLEET_AUTH_JWT_PUBLIC_KEY_PATH", "").strip()
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as key_file:
            return key_file.read()
    return None


_PUBLIC_KEY = _load_public_key() if _ENABLED else None


class DaoAuthMiddleware(BaseHTTPMiddleware):
    """/api/console/* 的 DAO JWT 强制校验；其余路径（含 FAP 转发）原样放行。"""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not _ENABLED or not path.startswith("/api/console/") or path == "/api/console/health":
            return await call_next(request)
        if _PUBLIC_KEY is None or not _ISSUER:
            return JSONResponse(
                {"detail": "认证未配置：需要 DAO JWT 公钥和 issuer"},
                status_code=500,
            )
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return JSONResponse({"detail": "未登录（缺少 DAO 访问令牌）"}, status_code=401)
        token = header[7:].strip()
        try:
            claims = jwt.decode(
                token, _PUBLIC_KEY, algorithms=["RS256"], issuer=_ISSUER,
                audience=_CLIENT_ID, options={"require": ["exp", "iss", "aud"]},
            )
            if claims.get("token_type") != "access" or claims.get("client_id") != _CLIENT_ID:
                raise jwt.InvalidTokenError("DAO access token binding is invalid")
        except jwt.PyJWTError:
            return JSONResponse({"detail": "令牌无效或已过期，请重新登录"}, status_code=401)
        uid = claims.get("user_id")
        if type(uid) is not int or uid <= 0:
            return JSONResponse({"detail": "令牌缺少有效 user_id"}, status_code=401)
        # 身份写入请求状态，路由内可读（验收人真实身份、审计归因的挂钩点）
        request.state.dao_user_id = uid
        request.state.dao_role = claims.get("role", "")
        return await call_next(request)
