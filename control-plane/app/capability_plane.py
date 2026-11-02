"""Runtime-neutral Capability Plane (ADR-0042).

The module contains no business-resource adapter. Runtime providers describe
operations and artifacts; the same contract drives discovery, invocation,
awareness, shared workspaces, and mixed DAG execution.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import time
import uuid
from typing import Any

from .db import pool as pg_pool

ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
RISK_ORDER = {"read": 0, "write": 1, "high_risk": 2}
PROVIDER_TYPES = {"mock", "agent", "mcp", "remote_agent"}
SENSITIVE_KEYS = {"password", "token", "secret", "api_key", "access_token", "refresh_token", "private_key"}


class CapabilityError(RuntimeError):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _assert_no_secret_fields(value: Any, path: str = "manifest") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in SENSITIVE_KEYS:
                raise CapabilityError(422, "SECRET_IN_MANIFEST", f"秘密字段只能使用 auth_handle：{path}.{key}")
            _assert_no_secret_fields(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_secret_fields(item, f"{path}[{index}]")


def _schema_validate(value: Any, schema: dict[str, Any], path: str = "input") -> None:
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            raise CapabilityError(422, "SCHEMA_MISMATCH", f"{path} 必须是 object")
        for key in schema.get("required") or []:
            if key not in value:
                raise CapabilityError(422, "SCHEMA_MISMATCH", f"{path} 缺少字段 {key}")
        for key, child in (schema.get("properties") or {}).items():
            if key in value:
                _schema_validate(value[key], child, f"{path}.{key}")
    elif kind == "array":
        if not isinstance(value, list):
            raise CapabilityError(422, "SCHEMA_MISMATCH", f"{path} 必须是 array")
        if schema.get("items"):
            for index, item in enumerate(value):
                _schema_validate(item, schema["items"], f"{path}[{index}]")
    elif kind == "string" and not isinstance(value, str):
        raise CapabilityError(422, "SCHEMA_MISMATCH", f"{path} 必须是 string")
    elif kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        raise CapabilityError(422, "SCHEMA_MISMATCH", f"{path} 必须是 integer")
    elif kind == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        raise CapabilityError(422, "SCHEMA_MISMATCH", f"{path} 必须是 number")
    elif kind == "boolean" and not isinstance(value, bool):
        raise CapabilityError(422, "SCHEMA_MISMATCH", f"{path} 必须是 boolean")


def validate_manifest(raw: dict[str, Any], *, allow_mock: bool) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CapabilityError(422, "INVALID_MANIFEST", "Provider Manifest 必须是 object")
    provider_id = raw.get("provider_id")
    provider_type = raw.get("provider_type")
    if not isinstance(provider_id, str) or not ID_RE.fullmatch(provider_id):
        raise CapabilityError(422, "INVALID_PROVIDER_ID", "provider_id 格式无效")
    if provider_type not in PROVIDER_TYPES:
        raise CapabilityError(422, "INVALID_PROVIDER_TYPE", f"provider_type 仅支持 {sorted(PROVIDER_TYPES)}")
    if provider_type == "mock" and not allow_mock:
        raise CapabilityError(422, "MOCK_PROVIDER_DISABLED", "mock provider 仅允许显式离线测试环境")
    capabilities = raw.get("capabilities")
    if not isinstance(capabilities, list) or not 1 <= len(capabilities) <= 100:
        raise CapabilityError(422, "INVALID_CAPABILITIES", "capabilities 必须包含 1–100 项")
    _assert_no_secret_fields(raw.get("config") or {}, "config")
    normalized = _copy(raw)
    normalized["capability_policy"] = raw.get("capability_policy") or "inherit_all"
    normalized["inherit_new_capabilities"] = raw.get("inherit_new_capabilities", True) is not False
    normalized["config"] = _copy(raw.get("config") or {})
    seen = set()
    for index, capability in enumerate(capabilities):
        if not isinstance(capability, dict):
            raise CapabilityError(422, "INVALID_CAPABILITY", f"capabilities[{index}] 必须是 object")
        operation_id = capability.get("operation_id")
        if not isinstance(operation_id, str) or not ID_RE.fullmatch(operation_id):
            raise CapabilityError(422, "INVALID_OPERATION_ID", f"capabilities[{index}].operation_id 无效")
        risk = capability.get("risk", "read")
        if risk not in RISK_ORDER:
            raise CapabilityError(422, "INVALID_RISK", f"{operation_id} risk 无效")
        capability_id = f"{provider_id}/{operation_id}"
        if capability_id in seen:
            raise CapabilityError(422, "DUPLICATE_CAPABILITY", capability_id)
        seen.add(capability_id)
        capability["capability_id"] = capability_id
        capability["risk"] = risk
        capability["version"] = int(capability.get("version") or 1)
        capability["input_schema"] = _copy(capability.get("input_schema") or {"type": "object"})
        capability["output_schema"] = _copy(capability.get("output_schema") or {})
        capability["input_artifacts"] = list(capability.get("input_artifacts") or [])
        capability["output_artifact"] = capability.get("output_artifact")
    normalized["capabilities"] = _copy(capabilities)
    return normalized


class MemoryStore:
    def __init__(self):
        self.providers: dict[str, dict[str, Any]] = {}
        self.workspaces: dict[int, dict[str, Any]] = {}
        self.invocations: list[dict[str, Any]] = []
        self.next_workspace_id = 1

    async def upsert_provider(self, manifest: dict[str, Any]) -> dict[str, Any]:
        provider_id = manifest["provider_id"]
        current = self.providers.get(provider_id)
        version = 1 if current is None or current["manifest"] != manifest else current["config_version"]
        if current is not None and current["manifest"] != manifest:
            version = current["config_version"] + 1
            current["health"] = {"status": "declared", "verified": False}
        row = current or {
            "provider_id": provider_id,
            "provider_type": manifest["provider_type"],
            "health": {"status": "declared", "verified": False},
            "created_at": time.time(),
        }
        row.update({
            "manifest": _copy(manifest),
            "provider_type": manifest["provider_type"],
            "config_version": version,
            "updated_at": time.time(),
        })
        self.providers[provider_id] = row
        return _copy(row)

    async def list_providers(self) -> list[dict[str, Any]]:
        return [_copy(row) for row in sorted(self.providers.values(), key=lambda row: row["provider_id"])]

    async def get_provider(self, provider_id: str) -> dict[str, Any] | None:
        row = self.providers.get(provider_id)
        return _copy(row) if row else None

    async def update_provider_health(self, provider_id: str, health: dict[str, Any]) -> None:
        self.providers[provider_id]["health"] = _copy(health)
        self.providers[provider_id]["updated_at"] = time.time()

    async def record_invocation(self, record: dict[str, Any]) -> dict[str, Any]:
        row = {"id": len(self.invocations) + 1, "created_at": time.time(), **_copy(record)}
        self.invocations.append(row)
        return _copy(row)

    async def provider_stats(self, provider_id: str) -> dict[str, Any]:
        rows = [row for row in self.invocations if row["provider_id"] == provider_id]
        succeeded = [row for row in rows if row["status"] == "succeeded"]
        durations = [row["duration_ms"] for row in rows]
        return {
            "invocations": len(rows),
            "successes": len(succeeded),
            "success_rate": len(succeeded) / len(rows) if rows else None,
            "avg_duration_ms": sum(durations) / len(durations) if durations else None,
            "recent_errors": [row["error"] for row in rows[-5:] if row.get("error")],
        }

    async def create_workspace(self, context: dict[str, Any]) -> dict[str, Any]:
        workspace_id = self.next_workspace_id
        self.next_workspace_id += 1
        row = {
            "id": workspace_id,
            "context": _copy(context),
            "version": 1,
            "status": "draft",
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        self.workspaces[workspace_id] = row
        return _copy(row)

    async def get_workspace(self, workspace_id: int) -> dict[str, Any] | None:
        row = self.workspaces.get(workspace_id)
        return _copy(row) if row else None

    async def update_workspace(self, workspace_id: int, expected_version: int, context: dict[str, Any], status: str) -> dict[str, Any]:
        row = self.workspaces[workspace_id]
        if row["version"] != expected_version:
            raise CapabilityError(409, "WORKSPACE_CONFLICT", "Workspace 已被其他执行更新")
        row["context"] = _copy(context)
        row["version"] += 1
        row["status"] = status
        row["updated_at"] = time.time()
        return _copy(row)


class PgStore:
    async def upsert_provider(self, manifest: dict[str, Any]) -> dict[str, Any]:
        async with (await pg_pool()).acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO fleet_capability_providers
                   (provider_id,provider_type,manifest,health)
                   VALUES ($1,$2,$3,'{"status":"declared","verified":false}')
                   ON CONFLICT (provider_id) DO UPDATE SET
                     provider_type=EXCLUDED.provider_type,
                     config_version=fleet_capability_providers.config_version
                       + CASE WHEN fleet_capability_providers.manifest=EXCLUDED.manifest THEN 0 ELSE 1 END,
                     manifest=EXCLUDED.manifest,
                     health=CASE WHEN fleet_capability_providers.manifest=EXCLUDED.manifest
                           THEN fleet_capability_providers.health
                           ELSE '{"status":"declared","verified":false}'::jsonb END,
                     updated_at=now()
                   RETURNING *""",
                manifest["provider_id"], manifest["provider_type"], json.dumps(manifest),
            )
        return self._provider(row)

    async def list_providers(self) -> list[dict[str, Any]]:
        async with (await pg_pool()).acquire() as conn:
            rows = await conn.fetch("SELECT * FROM fleet_capability_providers ORDER BY provider_id")
        return [self._provider(row) for row in rows]

    async def get_provider(self, provider_id: str) -> dict[str, Any] | None:
        async with (await pg_pool()).acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM fleet_capability_providers WHERE provider_id=$1", provider_id)
        return self._provider(row) if row else None

    async def update_provider_health(self, provider_id: str, health: dict[str, Any]) -> None:
        async with (await pg_pool()).acquire() as conn:
            await conn.execute(
                "UPDATE fleet_capability_providers SET health=$2,updated_at=now() WHERE provider_id=$1",
                provider_id, json.dumps(health),
            )

    async def record_invocation(self, record: dict[str, Any]) -> dict[str, Any]:
        async with (await pg_pool()).acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO fleet_capability_invocations
                   (provider_id,capability_id,args_digest,result_digest,status,error,duration_ms)
                   VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING *""",
                record["provider_id"], record["capability_id"], record["args_digest"],
                record["result_digest"], record["status"], record.get("error"), record["duration_ms"],
            )
        return dict(row)

    async def provider_stats(self, provider_id: str) -> dict[str, Any]:
        async with (await pg_pool()).acquire() as conn:
            row = await conn.fetchrow(
                """SELECT count(*) AS invocations,
                          count(*) FILTER (WHERE status='succeeded') AS successes,
                          avg(duration_ms) AS avg_duration_ms
                   FROM fleet_capability_invocations WHERE provider_id=$1""",
                provider_id,
            )
            errors = await conn.fetch(
                """SELECT error FROM fleet_capability_invocations
                   WHERE provider_id=$1 AND error IS NOT NULL ORDER BY id DESC LIMIT 5""",
                provider_id,
            )
        total = int(row["invocations"])
        return {
            "invocations": total,
            "successes": int(row["successes"]),
            "success_rate": int(row["successes"]) / total if total else None,
            "avg_duration_ms": float(row["avg_duration_ms"]) if row["avg_duration_ms"] is not None else None,
            "recent_errors": [item["error"] for item in errors],
        }

    async def create_workspace(self, context: dict[str, Any]) -> dict[str, Any]:
        async with (await pg_pool()).acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO fleet_workspaces (context) VALUES ($1) RETURNING *",
                json.dumps(context),
            )
        return self._workspace(row)

    async def get_workspace(self, workspace_id: int) -> dict[str, Any] | None:
        async with (await pg_pool()).acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM fleet_workspaces WHERE id=$1", workspace_id)
        return self._workspace(row) if row else None

    async def update_workspace(self, workspace_id: int, expected_version: int, context: dict[str, Any], status: str) -> dict[str, Any]:
        async with (await pg_pool()).acquire() as conn:
            row = await conn.fetchrow(
                """UPDATE fleet_workspaces SET context=$2,version=version+1,status=$3,updated_at=now()
                   WHERE id=$1 AND version=$4 RETURNING *""",
                workspace_id, json.dumps(context), status, expected_version,
            )
        if row is None:
            raise CapabilityError(409, "WORKSPACE_CONFLICT", "Workspace 已被其他执行更新或不存在")
        return self._workspace(row)

    @staticmethod
    def _provider(row) -> dict[str, Any]:
        result = dict(row)
        result["manifest"] = _json(result["manifest"])
        result["health"] = _json(result["health"])
        return result

    @staticmethod
    def _workspace(row) -> dict[str, Any]:
        result = dict(row)
        result["context"] = _json(result["context"])
        return result


def validate_plan(plan: dict[str, Any]) -> dict[str, Any]:
    nodes = plan.get("nodes")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 100:
        raise CapabilityError(422, "INVALID_PLAN", "plan.nodes 必须包含 1–100 个节点")
    ids = set()
    for node in nodes:
        node_id = node.get("id")
        if not isinstance(node_id, str) or not ID_RE.fullmatch(node_id):
            raise CapabilityError(422, "INVALID_NODE", "节点 id 无效")
        if node_id in ids:
            raise CapabilityError(422, "DUPLICATE_NODE", node_id)
        ids.add(node_id)
        if not node.get("capability_id"):
            raise CapabilityError(422, "INVALID_NODE", f"节点 {node_id} 缺 capability_id")
        node["input"] = _copy(node["input"]) if "input" in node else {}
        node["fallback_capability_ids"] = list(node.get("fallback_capability_ids") or [])
        node["on_failure"] = node.get("on_failure") or "stop"
        if node["on_failure"] not in {"stop", "replan", "continue"}:
            raise CapabilityError(422, "INVALID_NODE", f"节点 {node_id} on_failure 无效")
    edges = plan.get("edges") or []
    dependencies = {node_id: set() for node_id in ids}
    for edge in edges:
        source, target = edge.get("from"), edge.get("to")
        if source not in ids or target not in ids or source == target:
            raise CapabilityError(422, "INVALID_EDGE", f"无效边 {source}→{target}")
        dependencies[target].add(source)
    temporary, permanent = set(), set()

    def visit(node_id: str) -> None:
        if node_id in permanent:
            return
        if node_id in temporary:
            raise CapabilityError(422, "PLAN_CYCLE", "ExecutionPlan 必须是 DAG")
        temporary.add(node_id)
        for parent in dependencies[node_id]:
            visit(parent)
        temporary.remove(node_id)
        permanent.add(node_id)

    for node_id in ids:
        visit(node_id)
    normalized = _copy(plan)
    normalized["max_wall_seconds"] = float(plan.get("max_wall_seconds") or 300)
    normalized["max_invocations"] = int(plan.get("max_invocations") or len(nodes) * 3)
    if normalized["max_wall_seconds"] <= 0 or normalized["max_invocations"] <= 0:
        raise CapabilityError(422, "INVALID_PLAN_BUDGET", "计划预算必须为正数")
    return normalized


class CapabilityService:
    def __init__(self, store, *, allow_mock: bool = False, agent_client_factory=None):
        self.store = store
        self.allow_mock = allow_mock
        self.agent_client_factory = agent_client_factory
        self._workspace_locks: dict[int, asyncio.Lock] = {}

    def _lock(self, workspace_id: int) -> asyncio.Lock:
        if workspace_id not in self._workspace_locks:
            self._workspace_locks[workspace_id] = asyncio.Lock()
        return self._workspace_locks[workspace_id]

    async def register_provider(self, manifest: dict[str, Any]) -> dict[str, Any]:
        normalized = validate_manifest(manifest, allow_mock=self.allow_mock)
        return await self.store.upsert_provider(normalized)

    async def list_providers(self) -> list[dict[str, Any]]:
        return await self.store.list_providers()

    async def list_capabilities(self) -> list[dict[str, Any]]:
        result = []
        for provider in await self.store.list_providers():
            stats = await self.store.provider_stats(provider["provider_id"])
            for capability in provider["manifest"]["capabilities"]:
                result.append({
                    **_copy(capability),
                    "provider_id": provider["provider_id"],
                    "provider_type": provider["provider_type"],
                    "config_version": provider["config_version"],
                    "health": _copy(provider["health"]),
                    "performance": stats,
                })
        return result

    async def provider_card(self, provider_id: str) -> dict[str, Any]:
        provider = await self.store.get_provider(provider_id)
        if provider is None:
            raise CapabilityError(404, "PROVIDER_NOT_FOUND", provider_id)
        return {**provider, "performance": await self.store.provider_stats(provider_id)}

    async def verify_provider(self, provider_id: str) -> dict[str, Any]:
        provider = await self.store.get_provider(provider_id)
        if provider is None:
            raise CapabilityError(404, "PROVIDER_NOT_FOUND", provider_id)
        if provider["provider_type"] == "mock":
            health = {"status": "verified", "verified": True, "verification": "offline_contract"}
        elif provider["provider_type"] == "agent":
            health = await self._verify_agent(provider)
        elif provider["provider_type"] in {"remote_agent", "mcp"}:
            health = await self._verify_external_adapter(provider)
        else:
            health = {"status": "declared", "verified": False, "verification": "adapter_probe_required"}
        await self.store.update_provider_health(provider_id, health)
        return await self.provider_card(provider_id)

    async def _verify_agent(self, provider: dict[str, Any]) -> dict[str, Any]:
        if self.agent_client_factory is None:
            return {"status": "declared", "verified": False, "verification": "agent_adapter_unavailable"}
        client = self.agent_client_factory("")
        current = client.get_agent(provider["provider_id"])
        if not current or not current.get("registry"):
            return {"status": "unavailable", "verified": False, "verification": "agent_not_registered"}
        status = current["registry"].get("status")
        if status not in {"registered", "running"}:
            return {"status": "unavailable", "verified": False, "verification": f"agent_{status}"}
        return {
            "status": "verified",
            "verified": True,
            "verification": "registered_not_probed",
            "agent_config_version": current["registry"].get("config_version"),
            "yaml_sha256": current["registry"].get("yaml_sha256"),
        }

    async def _verify_external_adapter(self, provider: dict[str, Any]) -> dict[str, Any]:
        from .capability_adapters import McpAdapter, RemoteAgentAdapter

        adapter = RemoteAgentAdapter() if provider["provider_type"] == "remote_agent" else McpAdapter()
        return await adapter.verify(provider)

    async def resolve(self, request: dict[str, Any]) -> dict[str, Any]:
        risk_max = request.get("risk_max", "high_risk")
        if risk_max not in RISK_ORDER:
            raise CapabilityError(422, "INVALID_RISK", f"risk_max 无效：{risk_max}")
        maximum = RISK_ORDER[risk_max]
        candidates, shortfalls = [], []
        for capability in await self.list_capabilities():
            if request.get("operation_id") and capability["operation_id"] != request["operation_id"]:
                continue
            if request.get("output_artifact") and capability.get("output_artifact") != request["output_artifact"]:
                continue
            required_inputs = set(request.get("input_artifacts") or [])
            if required_inputs and not required_inputs.issubset(set(capability.get("input_artifacts") or [])):
                continue
            if RISK_ORDER[capability["risk"]] > maximum:
                continue
            if not capability["health"].get("verified"):
                shortfalls.append({"capability_id": capability["capability_id"], "reason": "not_verified"})
                continue
            performance = capability["performance"]
            score = (performance.get("success_rate") if performance.get("success_rate") is not None else 0.5)
            candidates.append({**capability, "resolution_score": score})
        candidates.sort(key=lambda item: (-item["resolution_score"], item["capability_id"]))
        return {"candidates": candidates, "shortfalls": shortfalls, "count": len(candidates)}

    async def invoke(
        self,
        capability_id: str,
        input_value: Any,
        *,
        confirmed: bool = False,
        authorization: str = "",
        actor: str = "runtime-user",
    ) -> dict[str, Any]:
        provider_id, operation_id = capability_id.split("/", 1) if "/" in capability_id else (None, None)
        if not provider_id:
            raise CapabilityError(422, "INVALID_CAPABILITY_ID", capability_id)
        provider = await self.store.get_provider(provider_id)
        if provider is None:
            raise CapabilityError(404, "PROVIDER_NOT_FOUND", provider_id)
        capability = next((item for item in provider["manifest"]["capabilities"] if item["operation_id"] == operation_id), None)
        if capability is None:
            raise CapabilityError(404, "CAPABILITY_NOT_FOUND", capability_id)
        if not provider["health"].get("verified"):
            raise CapabilityError(409, "CAPABILITY_UNAVAILABLE", "Provider 尚未通过运行验证", provider["health"])
        if RISK_ORDER[capability["risk"]] > 0 and not confirmed:
            raise CapabilityError(409, "CONFIRMATION_REQUIRED", f"{capability_id} 需要 confirmed=true")
        _schema_validate(input_value, capability["input_schema"])
        started = time.monotonic()
        args_digest = _digest(input_value)
        try:
            output, adapter_evidence = await self._invoke_adapter(
                provider, capability, input_value, authorization=authorization, actor=actor)
            _schema_validate(output, capability["output_schema"])
            status, error = "succeeded", None
        except CapabilityError as exc:
            output, adapter_evidence, status, error = None, {}, "failed", f"{exc.code}: {exc.message}"
            record = await self._record(provider_id, capability_id, args_digest, None, status, error, started)
            raise CapabilityError(exc.status, exc.code, exc.message, {"invocation_id": record["id"], **(exc.details or {})}) from exc
        record = await self._record(provider_id, capability_id, args_digest, _digest(output), status, error, started)
        return {
            "invocation_id": record["id"],
            "status": status,
            "capability_id": capability_id,
            "provider_id": provider_id,
            "provider_config_version": provider["config_version"],
            "output": output,
            "adapter_evidence": adapter_evidence,
            "duration_ms": record["duration_ms"],
        }

    async def _record(self, provider_id, capability_id, args_digest, result_digest, status, error, started) -> dict[str, Any]:
        return await self.store.record_invocation({
            "provider_id": provider_id,
            "capability_id": capability_id,
            "args_digest": args_digest,
            "result_digest": result_digest,
            "status": status,
            "error": error,
            "duration_ms": int((time.monotonic() - started) * 1000),
        })

    async def _invoke_adapter(self, provider, capability, input_value, *, authorization: str, actor: str):
        provider_type = provider["provider_type"]
        if provider_type == "mock":
            return self._invoke_mock(provider, capability, input_value), {"mode": "offline_mock"}
        if provider_type == "agent":
            return await self._invoke_agent(provider, capability, input_value, authorization, actor)
        if provider_type in {"remote_agent", "mcp"}:
            from .capability_adapters import McpAdapter, RemoteAgentAdapter

            adapter = RemoteAgentAdapter() if provider_type == "remote_agent" else McpAdapter()
            return await adapter.invoke(provider, capability, input_value)
        raise CapabilityError(501, "ADAPTER_UNAVAILABLE", f"Adapter {provider_type} 尚未实现")

    @staticmethod
    def _invoke_mock(provider, capability, input_value) -> Any:
        config = provider["manifest"].get("config") or {}
        mode = config.get("mode", "static")
        operation = capability["operation_id"]
        if mode == "static":
            if operation not in (config.get("results") or {}):
                raise CapabilityError(500, "MOCK_RESULT_MISSING", operation)
            return _copy(config["results"][operation])
        if mode == "echo":
            return _copy(input_value)
        if mode == "sum":
            numbers = []
            def collect(value):
                if isinstance(value, bool):
                    return
                if isinstance(value, (int, float)):
                    numbers.append(value)
                elif isinstance(value, dict):
                    for item in value.values():
                        collect(item)
                elif isinstance(value, list):
                    for item in value:
                        collect(item)
            collect(input_value)
            return {"total": sum(numbers)}
        if mode == "merge":
            return {"items": [_copy(input_value)]}
        if mode == "verify":
            return {"verified": True, "input": _copy(input_value)}
        if mode == "conflict":
            return {"status": "conflict", "input": _copy(input_value)}
        if mode == "fail":
            raise CapabilityError(502, "PROVIDER_FAILED", config.get("message") or "mock provider failure")
        raise CapabilityError(422, "INVALID_MOCK_MODE", mode)

    async def _invoke_agent(self, provider, capability, input_value, authorization: str, actor: str):
        if self.agent_client_factory is None:
            raise CapabilityError(501, "ADAPTER_UNAVAILABLE", "Agent Adapter 未配置")
        from evals.platform.execution import delivery_from_detail, parse_json_object

        client = self.agent_client_factory(authorization)
        model_profile = (provider["manifest"].get("config") or {}).get("model_profile") or "deepseek-default"
        prompt = f"""执行运行时能力调用。
operation_id={capability['operation_id']}
input={json.dumps(input_value, ensure_ascii=False)}
output_schema={json.dumps(capability['output_schema'], ensure_ascii=False)}
只输出符合 output_schema 的 JSON，不读取秘密，不修改无关资源。"""
        key = f"capability:{provider['provider_id']}:{capability['operation_id']}:{uuid.uuid4().hex}"
        task_id = client.dispatch(provider["provider_id"], prompt, model_profile, key, actor)
        deadline = time.monotonic() + 120
        while True:
            detail = client.task_detail(task_id)
            delivery = delivery_from_detail(detail)
            if delivery["state"] == "succeeded":
                raw = delivery["deliverable"]
                try:
                    output = parse_json_object(raw)
                except Exception:
                    output = {"output": raw}
                evidence = {"task_id": task_id, "loop_id": (delivery.get("loop") or {}).get("id")}
                return output, evidence
            if delivery["state"] in {"failed", "closed"}:
                raise CapabilityError(502, "PROVIDER_FAILED", f"Agent {delivery['state']}", {"task_id": task_id})
            if time.monotonic() >= deadline:
                client.close_task(task_id, f"{key}:timeout", actor)
                raise CapabilityError(504, "PROVIDER_TIMEOUT", "Agent 调用超时", {"task_id": task_id})
            await asyncio.sleep(1)

    async def create_workspace(self, body: dict[str, Any]) -> dict[str, Any]:
        goal = body.get("goal")
        if not isinstance(goal, str) or not goal.strip():
            raise CapabilityError(422, "INVALID_GOAL", "Workspace 需要非空 goal")
        context = {
            "workspace_id": f"ws-{uuid.uuid4().hex[:12]}",
            "goal": goal.strip(),
            "constraints": _copy(body.get("constraints") or []),
            "anti_goals": _copy(body.get("anti_goals") or []),
            "stop_conditions": _copy(body.get("stop_conditions") or []),
            "capability_snapshot": _copy(body.get("capability_snapshot") or await self.list_capabilities()),
            "evidence": [],
            "decisions": [],
            "events": [],
            "plan": None,
        }
        return await self.store.create_workspace(context)

    async def workspace(self, workspace_id: int) -> dict[str, Any]:
        workspace = await self.store.get_workspace(workspace_id)
        if workspace is None:
            raise CapabilityError(404, "WORKSPACE_NOT_FOUND", str(workspace_id))
        return workspace

    async def _update(self, workspace: dict[str, Any], status: str | None = None) -> dict[str, Any]:
        return await self.store.update_workspace(
            workspace["id"], workspace["version"], workspace["context"], status or workspace["status"])

    async def append_evidence(self, workspace_id: int, body: dict[str, Any]) -> dict[str, Any]:
        async with self._lock(workspace_id):
            workspace = await self.workspace(workspace_id)
            evidence = self._evidence_record(body)
            workspace["context"]["evidence"].append(evidence)
            updated = await self._update(workspace)
            return {"workspace": updated, "evidence": evidence}

    @staticmethod
    def _evidence_record(body: dict[str, Any]) -> dict[str, Any]:
        if "payload" not in body:
            raise CapabilityError(422, "INVALID_EVIDENCE", "Evidence 需要 payload")
        return {
            "evidence_id": f"ev-{uuid.uuid4().hex[:12]}",
            "kind": body.get("kind") or "observation",
            "artifact_type": body.get("artifact_type"),
            "producer": body.get("producer") or "runtime-user",
            "payload": _copy(body["payload"]),
            "source_refs": _copy(body.get("source_refs") or []),
            "created_at": time.time(),
        }

    async def save_plan(self, workspace_id: int, plan: dict[str, Any]) -> dict[str, Any]:
        async with self._lock(workspace_id):
            workspace = await self.workspace(workspace_id)
            normalized = validate_plan(plan)
            workspace["context"]["plan"] = normalized
            workspace["context"]["decisions"].append({
                "type": "plan_saved", "plan_digest": _digest(normalized), "created_at": time.time(),
            })
            updated = await self._update(workspace, "planned")
            return updated

    async def execute_plan(self, workspace_id: int, *, authorization: str = "", actor: str = "runtime-user") -> dict[str, Any]:
        async with self._lock(workspace_id):
            workspace = await self.workspace(workspace_id)
            plan = workspace["context"].get("plan")
            if not plan:
                raise CapabilityError(409, "PLAN_NOT_FOUND", "Workspace 尚未保存 ExecutionPlan")
            started = time.monotonic()
            invocation_count = 0
            outputs: dict[str, Any] = {}
            nodes = {node["id"]: node for node in plan["nodes"]}
            dependencies = {node_id: set() for node_id in nodes}
            for edge in plan.get("edges") or []:
                dependencies[edge["to"]].add(edge["from"])
            remaining = set(nodes)
            await self._update(workspace, "running")
            while remaining:
                if time.monotonic() - started >= plan["max_wall_seconds"]:
                    await self._update(workspace, "budget_exceeded")
                    raise CapabilityError(504, "PLAN_BUDGET_EXCEEDED", "ExecutionPlan 墙钟预算耗尽")
                ready = sorted(node_id for node_id in remaining if dependencies[node_id].issubset(outputs))
                if not ready:
                    raise CapabilityError(409, "PLAN_BLOCKED", "没有可继续节点", sorted(remaining))
                results = await asyncio.gather(*(
                    self._execute_node(
                        workspace, nodes[node_id], outputs,
                        authorization=authorization, actor=actor,
                    ) for node_id in ready
                ))
                invocation_count += sum(result.get("attempts", 1) for result in results)
                if invocation_count > plan["max_invocations"]:
                    workspace = await self.workspace(workspace_id)
                    await self._update(workspace, "budget_exceeded")
                    raise CapabilityError(429, "PLAN_BUDGET_EXCEEDED", "ExecutionPlan 调用预算耗尽")
                for node_id, result in zip(ready, results):
                    remaining.remove(node_id)
                    if result["status"] == "succeeded":
                        outputs[node_id] = result["output"]
                        continue
                    if result["on_failure"] == "continue":
                        outputs[node_id] = {"status": "skipped", "errors": result["errors"]}
                        continue
                    workspace = await self.workspace(workspace_id)
                    status = "needs_replan" if result["on_failure"] == "replan" else "failed"
                    updated = await self._update(workspace, status)
                    return {
                        "status": status,
                        "failed_node": node_id,
                        "errors": result["errors"],
                        "outputs": outputs,
                        "workspace": updated,
                    }
                workspace = await self.workspace(workspace_id)
            updated = await self._update(workspace, "completed")
            return {"status": "completed", "outputs": outputs, "workspace": updated}

    async def _execute_node(self, workspace, node, outputs, *, authorization: str, actor: str) -> dict[str, Any]:
        input_value = self._resolve_refs(node["input"], workspace, outputs)
        errors = []
        attempts = 0
        for capability_id in [node["capability_id"], *node["fallback_capability_ids"]]:
            attempts += 1
            try:
                result = await self.invoke(
                    capability_id, input_value,
                    confirmed=bool(node.get("confirmed")),
                    authorization=authorization,
                    actor=actor,
                )
                output = result["output"]
                for field in (node.get("acceptance") or {}).get("required_fields") or []:
                    if not isinstance(output, dict) or field not in output:
                        raise CapabilityError(502, "ACCEPTANCE_FAILED", f"节点 {node['id']} 缺输出字段 {field}")
                workspace = await self.workspace(workspace["id"])
                evidence = self._evidence_record({
                    "kind": "node_output",
                    "artifact_type": node.get("output_artifact"),
                    "producer": capability_id,
                    "payload": {"node_id": node["id"], "output": output, "invocation_id": result["invocation_id"]},
                    "source_refs": [f"node://{parent}/output" for parent in outputs],
                })
                workspace["context"]["evidence"].append(evidence)
                await self._update(workspace)
                return {"status": "succeeded", "output": output, "attempts": attempts}
            except CapabilityError as exc:
                errors.append({"capability_id": capability_id, "code": exc.code, "message": exc.message})
        workspace = await self.workspace(workspace["id"])
        evidence = self._evidence_record({
            "kind": "node_failure",
            "artifact_type": node.get("output_artifact"),
            "producer": node["id"],
            "payload": {"node_id": node["id"], "errors": errors},
        })
        workspace["context"]["evidence"].append(evidence)
        await self._update(workspace)
        return {"status": "failed", "on_failure": node["on_failure"], "errors": errors, "attempts": attempts}

    def _resolve_refs(self, value: Any, workspace: dict[str, Any], outputs: dict[str, Any]) -> Any:
        if isinstance(value, dict):
            return {key: self._resolve_refs(item, workspace, outputs) for key, item in value.items()}
        if isinstance(value, list):
            return [self._resolve_refs(item, workspace, outputs) for item in value]
        if not isinstance(value, str):
            return value
        if value.startswith("node://"):
            node_id = value[len("node://"):].split("/", 1)[0]
            if node_id not in outputs:
                raise CapabilityError(422, "INVALID_REFERENCE", value)
            return _copy(outputs[node_id])
        if value == "workspace://goal":
            return workspace["context"]["goal"]
        if value == "workspace://evidence":
            return _copy(workspace["context"]["evidence"])
        if value == "workspace://plan":
            return _copy(workspace["context"]["plan"])
        if value == "workspace://version":
            return workspace["version"]
        if value.startswith("workspace://evidence/"):
            producer = value.rsplit("/", 1)[-1]
            return [item for item in workspace["context"]["evidence"] if item.get("producer") == producer]
        if value.startswith("workspace://"):
            raise CapabilityError(422, "INVALID_REFERENCE", value)
        return value
