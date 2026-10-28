"""FAP v1 中间件（ADR-0011 H4/H5/H6）：幂等 / 限速 / PAT 鉴权。

挂载顺序（main.py，自外向内）：
    protocol_header → rate_limit → pat_auth → idempotency → 路由

- 幂等（任务 2）：写操作带 Idempotency-Key 时 24h 去重回放；键冲突 409。
- 限速（任务 3）：per-PAT（无 PAT per-IP）令牌桶。
- PAT（任务 5）：DeerFlow 原生 personal_access_tokens 表（同库）digest 校验，
  scope 仅 read/write；127.0.0.1 无 Authorization 的内部流量放行（QA-7）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time

from fastapi import Request
from fastapi.responses import JSONResponse

from .db import pool
from . import fap_context

logger = logging.getLogger("fleet.fap")

IDEMPOTENCY_WINDOW_HOURS = 24
IDEMPOTENCY_MAX_KEY_LEN = 255

RATE_LIMIT_PER_MINUTE = int(__import__("os").environ.get("FAP_RATE_PER_MINUTE", "60"))
_RATE_BUCKETS: dict[str, list[float]] = {}

# PAT scope 词表（QA-1 方案 A）：与网关原生路由权限串不同，控制面自校验。
PAT_SCOPE_READ = "read"
PAT_SCOPE_WRITE = "write"
_WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}
_PAT_TOKEN_RE = __import__("re").compile(r"^dfp_[0-9A-Za-z]+$")


def _peer(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# 可信反向代理网段/地址（本机 console backend、compose 内 console-backend 等）。
# 逗号分隔，支持单 IP 与 CIDR；可用 FAP_TRUSTED_PROXIES 覆盖。
import ipaddress as _ipaddress

_TRUSTED_NETWORKS = tuple(
    _ipaddress.ip_network(h.strip(), strict=False)
    for h in __import__("os").environ.get(
        "FAP_TRUSTED_PROXIES", "127.0.0.1,::1").split(",") if h.strip())


def _is_trusted_proxy(peer: str) -> bool:
    try:
        ip = _ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(ip in n for n in _TRUSTED_NETWORKS)


def _client_ip(request: Request) -> str:
    """真实客户端 IP：直连对端是可信 proxy（Console 后端同源转发）时取
    X-Forwarded-For 首跳；其余情况一律不信 XFF——否则远程客户端伪造
    XFF=127.0.0.1 即可冒充内部流量，静默击穿 PAT 强制（QA-7 口径）。"""
    peer = _peer(request)
    if _is_trusted_proxy(peer):
        xff = request.headers.get("x-forwarded-for")
        if xff:
            first = xff.split(",")[0].strip()
            if first:
                return first
    return peer


def _is_internal(request: Request) -> bool:
    """平台内部直连流量（Console 后端 _cp 拉数、dispatcher 本机调用）：
    无 Authorization、直连对端是可信 proxy、且未携带 XFF——fap_proxy 转发的
    客户端流量必重建 XFF，故带 XFF 的一律视为外部客户端，不享内部豁免。"""
    return (_bearer(request) is None
            and _is_trusted_proxy(_peer(request))
            and "x-forwarded-for" not in request.headers)


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


# ---------------------------------------------------------------- 幂等（H4 上半）

def _request_fingerprint(method: str, path: str, query: str, body: bytes) -> str:
    """只保存不可逆摘要，避免请求正文中的业务信息或秘密进入幂等表。"""
    import hashlib
    digest = hashlib.sha256()
    for part in (method.encode(), path.encode(), query.encode(), body):
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


async def _release_idempotency_placeholder(key, actor, created_at) -> None:
    """释放本次执行拥有的占位；created_at 防止误删后续已重建的占位。"""
    try:
        async with (await pool()).acquire() as conn:
            await conn.execute(
                """DELETE FROM fap_idempotency_keys
                   WHERE key=$1 AND actor=$2 AND created_at=$3 AND status_code IS NULL""",
                key, actor, created_at,
            )
    except Exception:  # noqa: BLE001
        logger.exception("idempotency placeholder release failed")


async def idempotency_middleware(request: Request, call_next):
    """先原子占位再执行；完成响应可回放，进行中或冲突请求返回 409。"""
    if request.method not in _WRITE_METHODS:
        return await call_next(request)
    key = request.headers.get("Idempotency-Key")
    if not key:
        return await call_next(request)
    if len(key) > IDEMPOTENCY_MAX_KEY_LEN:
        return JSONResponse({"detail": "Idempotency-Key 过长"}, status_code=400)
    actor = _actor_identity(request)
    fingerprint = _request_fingerprint(
        request.method, request.url.path, request.url.query, await request.body())

    # 占位事务只覆盖预留/回放判断，不包业务执行，避免长写请求占满连接池。
    async with (await pool()).acquire() as conn:
        async with conn.transaction():
            reservation = await conn.fetchrow(
                """INSERT INTO fap_idempotency_keys
                       (key, actor, method, path, request_digest)
                   VALUES ($1,$2,$3,$4,$5)
                   ON CONFLICT DO NOTHING RETURNING created_at""",
                key, actor, request.method, request.url.path, fingerprint,
            )
            if reservation is None:
                row = await conn.fetchrow(
                    """SELECT actor, status_code, response_json, request_digest, created_at
                       FROM fap_idempotency_keys WHERE key=$1 FOR UPDATE""",
                    key,
                )
                if row is None:  # 防御：占位冲突后对应行不应消失。
                    raise RuntimeError("idempotency reservation disappeared")
                if row["actor"] != actor:
                    return JSONResponse(
                        {"detail": "Idempotency-Key 已被其他 actor 使用"},
                        status_code=409,
                    )
                from datetime import datetime, timedelta, timezone
                fresh = row["created_at"] > datetime.now(timezone.utc) - timedelta(
                    hours=IDEMPOTENCY_WINDOW_HOURS)
                if fresh:
                    # 迁移前的记录没有正文摘要，只能按旧语义回放；新记录全部严格校验。
                    if row["request_digest"] and row["request_digest"] != fingerprint:
                        return JSONResponse(
                            {"detail": "Idempotency-Key 已用于不同请求"}, status_code=409)
                    if row["status_code"] is None:
                        return JSONResponse(
                            {"detail": "相同 Idempotency-Key 的请求正在执行，请稍后重试"},
                            status_code=409, headers={"Retry-After": "1"})
                    stored = row["response_json"]
                    if isinstance(stored, str):  # asyncpg 对 jsonb 默认返回原始字符串
                        stored = json.loads(stored)
                    resp = JSONResponse(stored, status_code=row["status_code"])
                    resp.headers["X-Idempotent-Replay"] = "true"
                    return resp
                # 过期键在同一锁事务内重建占位；并发重试只能有一个获得所有权。
                reservation = await conn.fetchrow(
                    """UPDATE fap_idempotency_keys
                       SET method=$3, path=$4, request_digest=$5,
                           status_code=NULL, response_json=NULL, created_at=now()
                       WHERE key=$1 AND actor=$2 RETURNING created_at""",
                    key, actor, request.method, request.url.path, fingerprint,
                )

    placeholder_created_at = reservation["created_at"]
    try:
        response = await call_next(request)
    except Exception:  # noqa: BLE001
        await _release_idempotency_placeholder(key, actor, placeholder_created_at)
        raise

    # 仅缓存 2xx/4xx；5xx 和异常释放占位，允许同键重试真正执行。
    if response.status_code >= 500:
        await _release_idempotency_placeholder(key, actor, placeholder_created_at)
        return response

    try:
        if hasattr(response, "body_iterator"):
            body = b""
            async for chunk in response.body_iterator:
                body += chunk
        else:  # 直接调用中间件的测试/内部调用可能返回普通 JSONResponse。
            body = response.body
        payload = json.loads(body.decode("utf-8") or "{}")
        async with (await pool()).acquire() as conn:
            updated = await conn.execute(
                """UPDATE fap_idempotency_keys
                   SET status_code=$4, response_json=$5::jsonb
                   WHERE key=$1 AND actor=$2 AND created_at=$3 AND status_code IS NULL""",
                key, actor, placeholder_created_at, response.status_code, json.dumps(payload),
            )
            if updated != "UPDATE 1":
                raise RuntimeError("idempotency placeholder changed before response store")
            # 顺手清窗口外的旧键
            await conn.execute(
                "DELETE FROM fap_idempotency_keys WHERE created_at < now() - ($1 || ' hours')::interval",
                str(IDEMPOTENCY_WINDOW_HOURS),
            )
        # 回放 body 后的响应需要重建
        rebuilt = JSONResponse(payload, status_code=response.status_code)
        for h, v in response.headers.items():
            if h.lower() not in ("content-length", "content-type"):
                rebuilt.headers[h] = v
        return rebuilt
    except Exception:  # noqa: BLE001
        logger.exception("idempotency store failed; response passes through unstored")
        await _release_idempotency_placeholder(key, actor, placeholder_created_at)
        return response


# ---------------------------------------------------------------- 审计透传（H5）

async def audit_passthrough_middleware(request: Request, call_next):
    """写操作 body 的 source/actor 自报字段 → contextvar → audit_events.detail。

    Starlette _CachedRequest 保证本层读 body 后下游端点仍能读到（QA-3 方案 a）。
    撞名处理分形态：**dict 形态**（FAP 自报）剥离出 body（留之会让 legacy
    端点字符串字段 422），只进审计；**字符串形态**（fleet_tasks.source /
    DispatchBody.actor）是 legacy 端点的业务字段，保留在 body 里照常落库，
    同时复制一份进审计 contextvar。
    """
    if request.method in _WRITE_METHODS:
        try:
            body = await request.body()
            data = json.loads(body.decode("utf-8")) if body else None
        except Exception:  # noqa: BLE001
            data = None
        if isinstance(data, dict):
            # 只剥离 dict 形态（FAP 自报格式，留之会让 legacy 端点字符串字段 422）；
            # 字符串形态是 legacy 端点的业务字段（fleet_tasks.source / DispatchBody.actor），
            # 保留在 body 里，仅同时记入审计 contextvar（2026-10-10 A2A 验收抓到的回归：
            # agent_spawn 的 source='agent' 被剥成默认 'web'，任务来源链路失真）。
            changed = False
            if (src := data.get("source")) is not None:
                fap_context.fap_source.set(src if isinstance(src, dict) else {"value": src})
                if isinstance(src, dict):
                    data.pop("source")
                    changed = True
            if (act := data.get("actor")) is not None:
                fap_context.fap_actor.set(act if isinstance(act, dict) else {"value": act})
                if isinstance(act, dict):
                    data.pop("actor")
                    changed = True
            if changed:
                # 剥离后回写 body（_CachedRequest 会把 _body 传给下游，见上）
                request._body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return await call_next(request)


# ---------------------------------------------------------------- 限速（H4 下半）

def _bucket_key(request: Request) -> str:
    token = _bearer(request)
    if token:
        return "pat:" + hashlib.sha256(token.encode()).hexdigest()[:16]
    return "ip:" + _client_ip(request)


async def rate_limit_middleware(request: Request, call_next):
    """简单令牌桶：per-PAT（无 PAT per-IP），60 req/min 默认可配。

    loopback 无 Authorization 的内部流量（Console 后端代理、dispatcher 本机
    调用）与 QA-7 的 PAT 放行同口径，不进限速桶——Console 多页 5s 轮询
    共享同一 ip 桶，60/min 会被 UI 自身打满。
    """
    if _is_internal(request):
        return await call_next(request)
    now = time.monotonic()
    window = 60.0
    limit = RATE_LIMIT_PER_MINUTE
    bk = _bucket_key(request)
    hits = _RATE_BUCKETS.setdefault(bk, [])
    cutoff = now - window
    while hits and hits[0] < cutoff:
        hits.pop(0)
    if len(hits) >= limit:
        retry_after = max(1, int(window - (now - hits[0])))
        return JSONResponse(
            {"error": "rate_limited", "retry_after": retry_after},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )
    hits.append(now)
    # 桶上限防内存膨胀：最多保留 2 倍窗口的 key 数
    if len(_RATE_BUCKETS) > 10000:
        _RATE_BUCKETS.clear()
    return await call_next(request)


# ---------------------------------------------------------------- PAT 鉴权（H6）

def _actor_identity(request: Request) -> str:
    """幂等键/审计的 actor 标识：PAT digest（启用后）或 client IP。"""
    token = _bearer(request)
    if token:
        return "pat:" + hashlib.sha256(token.encode()).hexdigest()[:16]
    return "ip:" + _client_ip(request)


async def _lookup_pat(token: str) -> dict | None:
    """DeerFlow 原生 PAT 表 digest 查找（同库，QA-1 方案 A）。"""
    digest = hashlib.sha256(token.encode()).hexdigest()
    async with (await pool()).acquire() as conn:
        row = await conn.fetchrow(
            """SELECT user_id, name, scopes, expires_at, revoked_at
               FROM personal_access_tokens WHERE token_digest=$1""",
            digest,
        )
    if row is None or row["revoked_at"] is not None:
        return None
    if row["expires_at"] is not None and row["expires_at"].timestamp() < time.time():
        return None
    raw_scopes = row["scopes"]
    if isinstance(raw_scopes, str):  # json 列 asyncpg 可能原样返回字符串
        import json as _json
        try:
            raw_scopes = _json.loads(raw_scopes)
        except ValueError:
            raw_scopes = []
    return {
        "user_id": row["user_id"],
        "name": row["name"],
        "scopes": set(raw_scopes or []),
    }


async def pat_auth_middleware(request: Request, call_next):
    """PAT 校验：写操作要 write scope，读操作要 read scope。

    放行（QA-7）：平台内部直连流量（Console 后端 _cp、dispatcher 本机调用），
    口径与限速豁免一致：可信 proxy 直连 + 无 Authorization + 无 XFF。
    """
    path = request.url.path
    # 自发现与文档永远免鉴权
    if path in ("/api/meta", "/api/agent-doc"):
        return await call_next(request)
    token = _bearer(request)
    if token is None:
        if _is_internal(request):
            return await call_next(request)
        return JSONResponse({"detail": "缺少 PAT（Authorization: Bearer <PAT>）"}, status_code=401)
    if not _PAT_TOKEN_RE.match(token):
        return JSONResponse({"detail": "PAT 格式非法"}, status_code=401)
    pat = await _lookup_pat(token)
    if pat is None:
        return JSONResponse({"detail": "PAT 无效或已吊销"}, status_code=401)
    need = PAT_SCOPE_WRITE if request.method in _WRITE_METHODS else PAT_SCOPE_READ
    if need not in pat["scopes"]:
        return JSONResponse(
            {"detail": f"PAT scope 不足：{request.method} 需要 {need}"},
            status_code=403,
        )
    request.state.fap_actor = f"pat:{pat['user_id']}:{pat['name']}"
    request.state.fap_pat = pat
    from . import fap_pats
    await fap_pats.touch_last_used(token)
    return await call_next(request)
