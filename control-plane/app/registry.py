"""Registry：agents/ YAML（一等公民）↔ fleet_agents 元数据 ↔ 网关 agents_api。

YAML 格式（apiVersion: agentfleet/v1）：
    metadata: name/owner/costCenter
    triggers: cron/im/web/agentCallable（平台调度语义）
    identity/budget: 平台侧护栏字段（P3 生效，先落库）
    spec: deerflow.custom-agent v1 导出包（spike 0002 验证的载体）

部署动作 = 把 spec 经 POST /api/agents/import 导入网关（幂等：同名覆盖语义
以网关为准，见 spike 0002 遗留项 1）。
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
from typing import Any

import httpx
import yaml

from .db import AGENTS_DIR, GATEWAY, audit, pool


class RegistryError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message


SYSTEM_AGENT_NAME = "fleet-ops-01"


def assert_mutable(name: str) -> None:
    """日常管理面不得改变系统 Agent；版本化部署直接调用 apply_agent。"""
    if name == SYSTEM_AGENT_NAME:
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
    spec = doc.get("spec") or {}
    if spec.get("format") != "deerflow.custom-agent" or spec.get("version") != 1:
        raise RegistryError(422, "spec must be a deerflow.custom-agent v1 package")
    return spec


async def sync_to_gateway(name: str, doc: dict[str, Any]) -> None:
    """把 spec 导入网关 agents_api（单服务身份 = 单一组织作用域，spike 0002）。

    幂等：import 遇 409（已存在）时回落 PUT 全量更新（spike 0002 遗留项 1 的
    工程解：部署动作可重放）。
    """
    pkg = _gateway_package(doc)
    async with httpx.AsyncClient(base_url=GATEWAY, timeout=30, trust_env=False) as client:
        r = await client.post("/api/agents/import", json=pkg)
        if r.status_code == 409:
            r = await client.put(f"/api/agents/{name}", json=pkg["agent"])
        if r.status_code not in (200, 201):
            raise RegistryError(502, f"gateway import failed: {r.status_code} {r.text[:300]}")


async def remove_from_gateway(name: str) -> None:
    async with httpx.AsyncClient(base_url=GATEWAY, timeout=30, trust_env=False) as client:
        r = await client.delete(f"/api/agents/{name}")
        if r.status_code not in (204, 404):
            raise RegistryError(502, f"gateway delete failed: {r.status_code} {r.text[:300]}")


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
