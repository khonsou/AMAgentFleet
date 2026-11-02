"""Registry：agents/ YAML（一等公民）↔ fleet_agents 元数据 ↔ RuntimeAdapter。

YAML 格式（apiVersion: agentfleet/v1）：
    metadata: name/owner/costCenter
    triggers: cron/im/web/agentCallable（平台调度语义）
    identity/budget: 平台侧护栏字段（P3 生效，先落库）
    spec: 当前 Runtime 的 Agent 配置包；既有 DeerFlow 格式保持兼容

部署动作由当前 Runtime 幂等执行，原生包格式与 API 路径不进入核心层。
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
from typing import Any

import httpx
import yaml

from .db import AGENTS_DIR, audit, pool
from .runtime import RuntimeServiceError, get_runtime


class RegistryError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message


SYSTEM_AGENT_NAME = "fleet-ops-01"
COMMANDER_AGENT_NAME = "fleet-cmd-01"
EVAL_AGENT_NAME = "fleet-eval-01"
READONLY_SYSTEM_AGENTS = {SYSTEM_AGENT_NAME, EVAL_AGENT_NAME}


def assert_mutable(name: str) -> None:
    """日常管理面不得改变系统 Agent；版本化部署直接调用 apply_agent。"""
    if name in READONLY_SYSTEM_AGENTS:
        raise RegistryError(403, f"系统内置 Agent '{name}' 只读，禁止通过日常管理接口修改或生命周期操作")


def _agents_dir() -> str:
    os.makedirs(AGENTS_DIR, exist_ok=True)
    return AGENTS_DIR


def yaml_path(name: str) -> str:
    safe = "".join(c for c in name if c.isalnum() or c in "-_")
    return os.path.join(_agents_dir(), f"{safe}.yaml")


def load_yaml_doc(name: str) -> dict[str, Any]:
    path = yaml_path(name)
    if not os.path.exists(path):
        raise RegistryError(404, f"agent YAML not found: {name}")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_all_yaml() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(glob.glob(os.path.join(_agents_dir(), "*.yaml"))):
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        if doc and doc.get("metadata", {}).get("name"):
            out[doc["metadata"]["name"]] = doc
    return out


def write_yaml_doc(doc: dict[str, Any]) -> str:
    meta = doc.get("metadata") or {}
    name = meta.get("name")
    if not name:
        raise RegistryError(422, "metadata.name required")
    path = yaml_path(name)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False)
    return path


def delete_yaml_doc(name: str) -> None:
    path = yaml_path(name)
    if os.path.exists(path):
        os.remove(path)


def sha256_doc(doc: dict[str, Any]) -> str:
    blob = json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()


def _gateway_package(doc: dict[str, Any]) -> dict[str, Any]:
    """兼容内部调用方；具体 Agent 包格式由当前 Runtime 校验。"""
    try:
        return get_runtime().agent_package(doc)
    except RuntimeServiceError as exc:
        raise RegistryError(exc.status, str(exc)) from exc


async def sync_to_gateway(name: str, doc: dict[str, Any]) -> None:
    """通过当前 Runtime 幂等部署 Agent，保留旧函数名作为 Registry 内部边界。"""
    try:
        await get_runtime().deploy_agent(name, doc, client_factory=httpx.AsyncClient)
    except RuntimeServiceError as exc:
        raise RegistryError(exc.status, str(exc)) from exc


async def remove_from_gateway(name: str) -> None:
    try:
        await get_runtime().remove_agent(name, client_factory=httpx.AsyncClient)
    except RuntimeServiceError as exc:
        raise RegistryError(exc.status, str(exc)) from exc


async def upsert_registry_row(name: str, doc: dict[str, Any]) -> None:
    meta = doc.get("metadata") or {}
    sha = sha256_doc(doc)
    async with (await pool()).acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO fleet_agents
               (name, display_name, owner, cost_center, status, yaml_sha256,
                triggers, identity, budget, egress, synced_at, updated_at)
               VALUES ($1,$2,$3,$4,'registered',$5,$6,$7,$8,$9,now(),now())
               ON CONFLICT (name) DO UPDATE SET
                 display_name=EXCLUDED.display_name,
                 owner=EXCLUDED.owner,
                 cost_center=EXCLUDED.cost_center,
                 yaml_sha256=EXCLUDED.yaml_sha256,
                 triggers=EXCLUDED.triggers,
                 identity=EXCLUDED.identity,
                 budget=EXCLUDED.budget,
                 egress=EXCLUDED.egress,
                 status='registered',
                 config_version=fleet_agents.config_version + 1,
                 synced_at=now(),
                 updated_at=now()
               RETURNING config_version""",
            name,
            meta.get("displayName") or meta.get("display_name") or "",
            meta.get("owner") or "",
            meta.get("costCenter") or meta.get("cost_center") or "",
            sha,
            json.dumps(doc.get("triggers") or {}),
            json.dumps(doc.get("identity") or {}),
            json.dumps(doc.get("budget") or {}),
            json.dumps(doc.get("egress") or []),
        )
    await audit("agent.registered", agent_name=name, actor="admin",
                detail={"yaml_sha256": sha, "config_version": row["config_version"]})


async def apply_agent(name: str, doc: dict[str, Any]) -> dict[str, Any]:
    """YAML 落盘 → 导入网关 → 元数据入库。一条链路的'部署'。"""
    write_yaml_doc(doc)
    await sync_to_gateway(name, doc)
    await upsert_registry_row(name, doc)
    async with (await pool()).acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM fleet_agents WHERE name=$1", name)
    return dict(row)


async def ensure_agent(name: str) -> None:
    """启动时按版本化 YAML 部署 Agent，并避免 Registry 版本无意义增长。

    Gateway 配置在独立数据卷中，可能因冷启动或卷重建而丢失，因此每次启动都
    同步 Gateway；仅在 Registry 缺失或摘要不一致时更新元数据。用户通过正常
    管理面更新 YAML 后，重启对账保留其修改，不会用仓库旧副本覆盖。
    """
    doc = load_yaml_doc(name)
    await sync_to_gateway(name, doc)
    async with (await pool()).acquire() as conn:
        current_sha = await conn.fetchval(
            "SELECT yaml_sha256 FROM fleet_agents WHERE name=$1", name)
    if current_sha != sha256_doc(doc):
        await upsert_registry_row(name, doc)


async def ensure_system_agent() -> None:
    await ensure_agent(SYSTEM_AGENT_NAME)


async def ensure_commander_agent() -> None:
    """指挥官随项目部署，但不属于只读系统 Agent。"""
    await ensure_agent(COMMANDER_AGENT_NAME)


async def ensure_eval_agent() -> None:
    """平台评测 Agent 随项目部署，但专用评测调用面独立验收。"""
    await ensure_agent(EVAL_AGENT_NAME)


async def ensure_versioned_agents() -> list[str]:
    """对账全部版本化 Agent；运行时注册的 Provider 不在此目录，不受影响。"""
    names = sorted(load_all_yaml())
    for name in names:
        await ensure_agent(name)
    return names


async def remove_agent(name: str) -> None:
    """真销毁：YAML + 网关配置 + 注册表行全删（与前端确认框"元数据将被清除"一致）。

    fleet_tasks.agent_name 是软引用（外键已降级），历史任务/审计不受影响；
    fleet_inbox 是硬外键——先清该 agent 的收件箱再删注册表行（FK 约束）。"""
    delete_yaml_doc(name)
    await remove_from_gateway(name)
    async with (await pool()).acquire() as conn:
        await conn.execute("DELETE FROM fleet_inbox WHERE agent_name=$1", name)
        await conn.execute("DELETE FROM fleet_agents WHERE name=$1", name)
    await audit("agent.terminated", agent_name=name, actor="admin")


async def list_registry() -> list[dict[str, Any]]:
    async with (await pool()).acquire() as conn:
        rows = await conn.fetch("SELECT * FROM fleet_agents ORDER BY name")
    return [dict(r) for r in rows]
