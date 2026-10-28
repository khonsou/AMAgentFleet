"""系统 Fleet 操作凭证 Vault、人工轮换与服务端执行代理（ADR-0018）。

原始 PAT 只在控制面内存中短暂出现；PG 同时保存 PAT digest 与 Vault 密文，
Runner 只调用内部执行端点，不接触 Authorization。
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .db import GATEWAY, audit, pool
from .fap_pats import _assert_loopback, _digest, _new_token
from .fleet_ops import load_operations_protocol
from .registry import SYSTEM_AGENT_NAME

log = logging.getLogger("fleet.control-plane")

router = APIRouter(tags=["fleet-ops-credentials"])

SYSTEM_OPS_USER_ID = f"fap:system:{SYSTEM_AGENT_NAME}"
SYSTEM_OPS_PAT_ID = "fap-system-fleet-ops-01"
SYSTEM_OPS_CREDENTIAL_NAME = SYSTEM_AGENT_NAME
SYSTEM_OPS_SCOPES = ["read", "write"]
CONTROL_PLANE_SELF = os.environ.get("FLEET_CONTROL_PLANE_SELF_URL", "http://127.0.0.1:8003")
CONSOLE = os.environ.get("FLEET_CONSOLE_URL", "http://127.0.0.1:8002")


class CredentialVault(ABC):
    """KMS 适配边界：生产实现只需提供相同的 encrypt/decrypt 契约。"""

    @abstractmethod
    def encrypt(self, value: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def decrypt(self, ciphertext: str) -> str:
        raise NotImplementedError


class LocalFernetVault(CredentialVault):
    """本地开发 Vault；密钥独立落盘且权限固定为 0600，不进入 git。"""

    def __init__(self, key_path: str | None = None):
        default = Path(__file__).resolve().parents[1] / "secrets" / "ops-vault.key"
        self.key_path = Path(key_path or os.environ.get("FLEET_OPS_VAULT_KEY_PATH", default))
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if self.key_path.exists():
            key = self.key_path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            try:
                fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                key = self.key_path.read_bytes().strip()
            else:
                with os.fdopen(fd, "wb") as f:
                    f.write(key + b"\n")
        os.chmod(self.key_path, 0o600)
        self._fernet = Fernet(key)

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode()).decode()


def _vault() -> CredentialVault:
    return LocalFernetVault()


async def credential_status() -> dict[str, Any]:
    async with (await pool()).acquire() as conn:
        row = await conn.fetchrow(
            """SELECT c.pat_id, c.version, c.created_at, c.rotated_at,
                      p.scopes, p.revoked_at, p.last_used_at
               FROM fleet_system_credentials c
               JOIN personal_access_tokens p ON p.id=c.pat_id
               WHERE c.name=$1 AND p.user_id=$2""",
            SYSTEM_OPS_CREDENTIAL_NAME, SYSTEM_OPS_USER_ID,
        )
    if row is None:
        raise HTTPException(503, "系统 Fleet 操作凭证尚未配置")
    scopes = row["scopes"]
    if isinstance(scopes, str):
        import json
        scopes = json.loads(scopes)
    return {
        "agent": SYSTEM_AGENT_NAME,
        "system": True,
        "configured": row["revoked_at"] is None,
        "pat_id": row["pat_id"],
        "scopes": scopes,
        "version": row["version"],
        "created_at": row["created_at"],
        "rotated_at": row["rotated_at"],
        "last_used_at": row["last_used_at"],
    }


async def rotate_system_credential(actor: str = "console") -> dict[str, Any]:
    """在同一事务内替换 digest 与密文；旧 token 立即失效，响应不回显新 token。"""
    token = _new_token()
    ciphertext = _vault().encrypt(token)
    async with (await pool()).acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO personal_access_tokens
                   (id, user_id, name, token_digest, scopes, expires_at, created_at, revoked_at)
                   VALUES ($1,$2,$3,$4,to_jsonb($5::text[]),NULL,now(),NULL)
                   ON CONFLICT (id) DO UPDATE SET
                     user_id=EXCLUDED.user_id, name=EXCLUDED.name,
                     token_digest=EXCLUDED.token_digest, scopes=EXCLUDED.scopes,
                     expires_at=NULL, created_at=now(), revoked_at=NULL""",
                SYSTEM_OPS_PAT_ID, SYSTEM_OPS_USER_ID, "Fleet 操作助手（系统内置）",
                _digest(token), SYSTEM_OPS_SCOPES,
            )
            await conn.execute(
                """INSERT INTO fleet_system_credentials
                   (name, pat_id, ciphertext, version, created_at, rotated_at)
                   VALUES ($1,$2,$3,1,now(),now())
                   ON CONFLICT (name) DO UPDATE SET
                     pat_id=EXCLUDED.pat_id, ciphertext=EXCLUDED.ciphertext,
                     version=fleet_system_credentials.version+1, rotated_at=now()""",
                SYSTEM_OPS_CREDENTIAL_NAME, SYSTEM_OPS_PAT_ID, ciphertext,
            )
    await audit("pat.system_rotated", agent_name=SYSTEM_AGENT_NAME, actor=actor,
                detail={"pat_id": SYSTEM_OPS_PAT_ID, "scopes": SYSTEM_OPS_SCOPES})
    return await credential_status()


async def ensure_system_credential() -> dict[str, Any]:
    async with (await pool()).acquire() as conn:
        configured = await conn.fetchval(
            "SELECT 1 FROM fleet_system_credentials WHERE name=$1", SYSTEM_OPS_CREDENTIAL_NAME)
    if configured:
        return await credential_status()
    return await rotate_system_credential(actor="system")


@router.get("/api/ops/credential")
async def system_credential_status(request: Request) -> dict[str, Any]:
    _assert_loopback(request)
    return await ensure_system_credential()


@router.post("/api/ops/credential/rotate")
async def system_credential_rotate(request: Request) -> dict[str, Any]:
    _assert_loopback(request)
    return await rotate_system_credential(actor="console")


async def _system_token() -> str:
    await ensure_system_credential()
    async with (await pool()).acquire() as conn:
        ciphertext = await conn.fetchval(
            "SELECT ciphertext FROM fleet_system_credentials WHERE name=$1",
            SYSTEM_OPS_CREDENTIAL_NAME,
        )
    if not ciphertext:
        raise HTTPException(503, "系统 Fleet 操作凭证不可用")
    try:
        return _vault().decrypt(ciphertext)
    except InvalidToken:
        raise HTTPException(
            503,
            "系统 Fleet 操作凭证无法解密：Vault 密钥与 PG 中的密文不匹配，"
            "请在 Console 重新轮换系统凭证",
        ) from None


async def check_system_credential_readable() -> None:
    """启动自检：密钥与密文不匹配时只告警，不阻断控制面（轮换需人工确认）。"""
    async with (await pool()).acquire() as conn:
        ciphertext = await conn.fetchval(
            "SELECT ciphertext FROM fleet_system_credentials WHERE name=$1",
            SYSTEM_OPS_CREDENTIAL_NAME,
        )
    if not ciphertext:
        return
    try:
        _vault().decrypt(ciphertext)
    except InvalidToken:
        log.warning(
            "system credential ciphertext cannot be decrypted with the current vault key; "
            "Fleet ops calls will return 503 until the credential is rotated"
        )


class OpsExecuteBody(BaseModel):
    operation_id: str = Field(min_length=1)
    path_params: dict[str, Any] = Field(default_factory=dict)
    query: dict[str, Any] = Field(default_factory=dict)
    body: dict[str, Any] | None = None
    confirmed: bool = False
    idempotency_key: str | None = None


def _operation(operation_id: str) -> dict[str, Any]:
    for operation in load_operations_protocol()["operations"]:
        if operation["id"] == operation_id:
            return operation
    raise HTTPException(404, f"未登记的 Fleet 操作：{operation_id}")


@router.post("/internal/ops/execute")
async def execute_operation(request: Request, payload: OpsExecuteBody) -> dict[str, Any]:
    """仅供内部 Runner 工具调用；业务 Control Plane surface 必须过 PAT middleware。"""
    from .fap_middleware import _is_internal
    if not _is_internal(request):
        raise HTTPException(403, "Fleet 执行代理仅限内部服务调用")
    operation = _operation(payload.operation_id)
    if operation.get("confirmation") == "human_only":
        raise HTTPException(403, f"该操作必须在页面人工完成：{operation['description']}")
    if operation.get("write") and not payload.confirmed:
        raise HTTPException(409, "写操作必须获得用户明确确认并设置 confirmed=true")
    for field in operation.get("secret_fields") or []:
        if payload.body and payload.body.get(field):
            raise HTTPException(422, f"字段 {field} 含秘密，不得进入 Agent Thread")

    path = operation["path"]
    for key, value in payload.path_params.items():
        path = path.replace("{" + key + "}", quote(str(value), safe=""))
    if "{" in path or "}" in path:
        raise HTTPException(422, f"路径参数不完整：{path}")
    headers = {"Idempotency-Key": payload.idempotency_key} if payload.idempotency_key else {}
    surface = operation.get("surface", "control-plane")
    if surface == "control-plane":
        headers["Authorization"] = f"Bearer {await _system_token()}"
    bases = {"control-plane": CONTROL_PLANE_SELF, "console": CONSOLE, "gateway": GATEWAY}
    timeout = min(max(float(operation.get("timeout_seconds", 60)), 5), 600)
    async with httpx.AsyncClient(base_url=bases[surface], timeout=timeout, trust_env=False) as client:
        response = await client.request(
            operation["method"], path, params=payload.query, json=payload.body, headers=headers)
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
