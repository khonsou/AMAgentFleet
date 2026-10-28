"""FAP PAT 管理端点（ADR-0011 H6 + F3，QA-1 方案 A）。

- 直接读写 DeerFlow 原生 `personal_access_tokens` 表（同库 deerflow，已核实）；
  网关原生 scope 词表（threads:read 等路由权限串）与 FAP 的 read/write 不同，
  本端点签发 FAP 词表 PAT，由控制面 fap_middleware 自校验，不经网关 authz。
- 安全：签发/吊销/列表是管理操作——仅限 loopback 调用（Console 后端代理），
  非 loopback 一律 403（即使持 PAT；FAP 实例的首个 PAT 经 Console 本机签发）。
- token 只回显一次，库中只存 SHA-256 digest（与网关 PAT 同构）。
"""

from __future__ import annotations

import hashlib
import re
import secrets
import string
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .db import audit, pool

router = APIRouter(tags=["fap-pat"])

PAT_PREFIX = "dfp_"
_B62 = string.digits + string.ascii_letters
_ALLOWED_SCOPES = {"read", "write"}

# 管理端点仅限平台内部调用（Console 后端 _cp 路径：可信 proxy 直连、无 XFF）。
# 系统执行代理带 PAT 时也必须是本机直连；经同源代理的第三方流量必带 XFF，拒绝。
def _assert_loopback(request: Request) -> None:
    from .fap_middleware import _is_internal, _is_trusted_proxy, _peer
    if _is_internal(request):
        return
    pat = getattr(request.state, "fap_pat", None)
    if (pat and pat.get("user_id") == "fap:system:fleet-ops-01"
            and _is_trusted_proxy(_peer(request)) and "x-forwarded-for" not in request.headers):
        return
    raise HTTPException(403, "PAT 管理端点仅限 Console 本机调用")


def _new_token() -> str:
    return PAT_PREFIX + "".join(secrets.choice(_B62) for _ in range(43))  # ~256 bit


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class PatCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    scopes: list[str]
    expires_in_days: int | None = None
    issuer: str = "console"


def _validate_scopes(scopes: list[str]) -> list[str]:
    unknown = set(scopes) - _ALLOWED_SCOPES
    if unknown:
        raise HTTPException(422, f"FAP PAT scope 仅支持 read/write，收到: {sorted(unknown)}")
    dedup = sorted(set(scopes))
    if not dedup:
        raise HTTPException(422, "scope 不能为空")
    return dedup


@router.post("/api/pats", status_code=201)
async def pat_create(request: Request, body: PatCreateBody) -> dict:
    """签发 PAT：token 仅此一次回显。系统身份只能由专用轮换路径维护。"""
    _assert_loopback(request)
    if body.issuer.startswith("system:"):
        raise HTTPException(403, "系统 PAT 身份不能通过普通签发接口冒用")
    scopes = _validate_scopes(body.scopes)
    token = _new_token()
    async with (await pool()).acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO personal_access_tokens (id, user_id, name, token_digest, scopes, expires_at, created_at)
               VALUES ($1,$2,$3,$4, to_jsonb($5::text[]), CASE WHEN $6::int IS NULL THEN NULL ELSE now() + ($6 || ' days')::interval END, now())
               RETURNING id, created_at""",
            f"fap-{secrets.token_hex(8)}", f"fap:{body.issuer}", body.name,
            _digest(token), scopes, body.expires_in_days,
        )
    await audit("pat.issued", actor=body.issuer,
                detail={"pat_id": row["id"], "name": body.name, "scopes": scopes})
    return {"id": row["id"], "name": body.name, "scopes": scopes,
            "token": token, "created_at": row["created_at"],
            "expires_in_days": body.expires_in_days}


@router.get("/api/pats")
async def pat_list(request: Request) -> list[dict]:
    """列出现存 PAT（不含 digest/token；前缀提示用于对账）。"""
    _assert_loopback(request)
    async with (await pool()).acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, user_id, name, scopes, expires_at, revoked_at, last_used_at, created_at
               FROM personal_access_tokens
               WHERE user_id LIKE 'fap:%'
               ORDER BY created_at DESC""")
    import json as _json
    out = []
    for r in rows:
        scopes = r["scopes"]
        if isinstance(scopes, str):  #  json 列 asyncpg 返回原始字符串（jsonb 才自动解析）
            try:
                scopes = _json.loads(scopes)
            except ValueError:
                scopes = []
        out.append(
            {"id": r["id"], "name": r["name"], "scopes": scopes,
             "system": r["user_id"] == "fap:system:fleet-ops-01",
             "prefix": PAT_PREFIX, "expires_at": r["expires_at"], "revoked_at": r["revoked_at"],
             "last_used_at": r["last_used_at"], "created_at": r["created_at"]}
        )
    return out


@router.delete("/api/pats/{pat_id}")
async def pat_revoke(request: Request, pat_id: str, author: str = "console") -> dict:
    _assert_loopback(request)
    if pat_id == "fap-system-fleet-ops-01":
        raise HTTPException(403, "系统 PAT 不可吊销；请在 Settings 人工轮换")
    if not re.fullmatch(r"fap-[0-9a-f]{16}", pat_id):
        raise HTTPException(404, "PAT 不存在")
    async with (await pool()).acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE personal_access_tokens SET revoked_at=now()
               WHERE id=$1 AND user_id LIKE 'fap:%' AND user_id != 'fap:system:fleet-ops-01'
                 AND revoked_at IS NULL
               RETURNING name""", pat_id)
    if row is None:
        raise HTTPException(404, "PAT 不存在或已吊销")
    await audit("pat.revoked", actor=author, detail={"pat_id": pat_id, "name": row["name"]})
    return {"status": "revoked"}


# ---- 校验侧（fap_middleware 使用） ----

_last_used_writes: dict[str, float] = {}
_LAST_USED_THROTTLE = 300.0


async def touch_last_used(token: str) -> None:
    """best-effort 节流更新 last_used_at（同网关 PAT 语义）。"""
    now = time.monotonic()
    dg = _digest(token)
    if now - _last_used_writes.get(dg, 0) < _LAST_USED_THROTTLE:
        return
    _last_used_writes[dg] = now
    try:
        async with (await pool()).acquire() as conn:
            await conn.execute(
                "UPDATE personal_access_tokens SET last_used_at=now() WHERE token_digest=$1", dg)
    except Exception:  # noqa: BLE001
        pass
