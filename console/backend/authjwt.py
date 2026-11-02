"""DAO（angrymiao-auth）员工 OAuth JWT 校验（P4 人身份路径）。

接入规格参照 timeline 项目 docs/oauth-auth-integration.md（同一 OAuth 平台）：
  - RS256 公钥本地验签，校验 issuer/audience，仅接受 Fleet access token；
  - claims 约定与 coin backend 一致：user_id(int) / role / exp / iss；
  - 前端经 PKCE 整页跳转取得 access token（纯内存），调用 Console API 时带
    Authorization: Bearer <JWT>；本中间件保护 /api/console/*（FAP /api/* 仍走 PAT，
    两套凭据并存：人=DAO JWT，第三方 agent=PAT）。

员工身份（ADR-0041）：真实 DAO token 不携带 role=inhouse，因此验签通过后用同一
access token 调 coin 分组接口，返回 ids 含 FLEET_INHOUSE_GROUP_ID 才放行。

开关与环境（compose / .env 注入）：
  FLEET_AUTH_ENABLED=1              启用；缺省 0（开发态现状不受影响）
  FLEET_AUTH_JWT_PUBLIC_KEY_PEM     RSA 公钥 PEM 文本（与 _PATH 二选一）
  FLEET_AUTH_JWT_PUBLIC_KEY_PATH    RSA 公钥 PEM 文件路径
  FLEET_AUTH_JWT_ISSUER             必填；与 DAO 配置一致
  FLEET_INHOUSE_GROUP_API_URL       coin 分组接口；缺省见 _GROUP_API_URL
  FLEET_INHOUSE_GROUP_ID            inhouse 分组 id；缺省 2
  FLEET_INHOUSE_TIMEOUT_SECONDS     分组接口超时；缺省 3
  FLEET_INHOUSE_CACHE_SECONDS       分组结果缓存；缺省 60

豁免：
  - /api/console/health            容器健康检查
"""

from __future__ import annotations

import hashlib
import os
import time

import httpx
import jwt
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

_ENABLED = os.environ.get("FLEET_AUTH_ENABLED", "0") == "1"
_ISSUER = os.environ.get("FLEET_AUTH_JWT_ISSUER", "")
_CLIENT_ID = "am-agent-fleet"
_GROUP_API_URL = os.environ.get(
    "FLEET_INHOUSE_GROUP_API_URL",
    "https://miao-coin.angrymiao.com/api/group/current-user",
)
_GROUP_ID = int(os.environ.get("FLEET_INHOUSE_GROUP_ID", "2"))
_GROUP_TIMEOUT = float(os.environ.get("FLEET_INHOUSE_TIMEOUT_SECONDS", "3"))
_GROUP_CACHE_SECONDS = float(os.environ.get("FLEET_INHOUSE_CACHE_SECONDS", "60"))

# token SHA-256 -> (monotonic 过期时间, 是否员工)；只缓存成功查询的结果
_group_cache: dict[str, tuple[float, bool]] = {}


class InhouseCheckUnavailable(Exception):
    """coin 分组接口不可用或响应非法（fail-closed 到 503）。"""


async def _fetch_group_ids(token: str) -> list:
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=_GROUP_TIMEOUT) as client:
            response = await client.get(
                _GROUP_API_URL, headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        raise InhouseCheckUnavailable("coin group api request failed") from exc
    if response.status_code != 200:
        raise InhouseCheckUnavailable(f"coin group api HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise InhouseCheckUnavailable("coin group api returned non-json") from exc
    ids = payload.get("ids") if isinstance(payload, dict) else None
    if not isinstance(ids, list):
        raise InhouseCheckUnavailable("coin group api response missing ids")
    return ids


async def is_inhouse_member(token: str) -> bool:
    key = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = time.monotonic()
    cached = _group_cache.get(key)
    if cached and cached[0] > now:
        return cached[1]
    ids = await _fetch_group_ids(token)
    member = any(type(group_id) is int and group_id == _GROUP_ID for group_id in ids)
    if len(_group_cache) >= 1024:
        # 条目按 token 累积；清空比逐条淘汰更简单，代价只是下一次重新查询
        _group_cache.clear()
    _group_cache[key] = (now + _GROUP_CACHE_SECONDS, member)
    return member


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
        try:
            inhouse = await is_inhouse_member(token)
        except InhouseCheckUnavailable:
            return JSONResponse(
                {"detail": "员工身份校验服务不可用，请稍后重试"}, status_code=503,
            )
        if not inhouse:
            return JSONResponse({"detail": "仅限 inhouse 员工访问 Console"}, status_code=403)
        # 身份写入请求状态，路由内可读（验收人真实身份、审计归因的挂钩点）
        request.state.dao_user_id = uid
        request.state.dao_role = claims.get("role", "")
        request.state.dao_inhouse = True
        return await call_next(request)
