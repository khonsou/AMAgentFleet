"""DeerFlow/LangGraph implementation of the Fleet RuntimeAdapter."""

from __future__ import annotations

from typing import Any

import httpx

from ..db import GATEWAY
from .base import (
    RunRequest, RunResult, RuntimeAdapter, RuntimeCapabilities,
    RuntimeServiceError, classify_runtime_failure,
)


class DeerFlowAdapter(RuntimeAdapter):
    name = "deerflow"

    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or GATEWAY

    def _client(self, factory, timeout: float):
        return (factory or httpx.AsyncClient)(
            base_url=self.base_url, timeout=timeout, trust_env=False)

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            resume=True, stream=True, cancel=True, feedback=True,
            artifacts=True, subagents=True, managed_models=True, legacy_crons=True,
        )

    def agent_package(self, document: dict[str, Any]) -> dict[str, Any]:
        spec = document.get("spec") or {}
        if spec.get("format") != "deerflow.custom-agent" or spec.get("version") != 1:
            raise RuntimeServiceError(422, "spec must be a deerflow.custom-agent v1 package")
        return spec

    async def deploy_agent(self, name: str, document: dict[str, Any], *, client_factory=None) -> None:
        package = self.agent_package(document)
        async with self._client(client_factory, 30) as client:
            response = await client.post("/api/agents/import", json=package)
            if response.status_code == 409:
                response = await client.put(f"/api/agents/{name}", json=package["agent"])
        if response.status_code not in (200, 201):
            raise RuntimeServiceError(502, f"gateway import failed: {response.status_code} {response.text[:300]}")

    async def remove_agent(self, name: str, *, client_factory=None) -> None:
        async with self._client(client_factory, 30) as client:
            response = await client.delete(f"/api/agents/{name}")
        if response.status_code not in (204, 404):
            raise RuntimeServiceError(502, f"gateway delete failed: {response.status_code} {response.text[:300]}")

    def _run_body(self, request: RunRequest, recovery_instruction: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "assistant_id": request.agent_name,
            "input": {"messages": [{"role": "user", "content": (
                recovery_instruction if request.resuming else request.input_text
            )}]},
            "on_disconnect": "continue",
            "metadata": {
                "fleet_loop_id": request.loop_id,
                "fleet_recovery": request.recovery_action or "initial",
            },
            "config": {
                "recursion_limit": request.recursion_limit,
                "configurable": {
                    "thread_id": request.thread_id,
                    "subagent_enabled": request.subagent_enabled,
                },
            },
        }
        if request.model_profile:
            body["context"] = {"model_name": request.model_profile}
        if request.resuming:
            body["checkpoint_id"] = request.checkpoint_ref
        return body

    async def execute_run(self, request: RunRequest, *, client_factory=None,
                          recovery_instruction: str = "") -> RunResult:
        body = self._run_body(request, recovery_instruction)
        async with self._client(client_factory, 30 * 60 + 60) as client:
            response = await client.post("/api/runs/wait", json=body)
        if response.status_code != 200:
            message = f"gateway {response.status_code}: {response.text[:300]}"
            raise RuntimeServiceError(
                response.status_code, message,
                kind=classify_runtime_failure(message, status_code=response.status_code),
            )
        result = response.json()
        if result.get("status") == "error":
            message = str(result.get("error"))[:500]
            raise RuntimeServiceError(200, message, kind=classify_runtime_failure(message))
        return RunResult(
            text=self._preview(result),
            tokens=self._tokens(result),
            artifacts=[str(path) for path in result.get("artifacts") or []],
            messages=result.get("messages") or [],
            run_id=result.get("run_id"),
            checkpoint_ref=result.get("checkpoint_id"),
        )

    @staticmethod
    def _tokens(result: dict[str, Any]) -> int | None:
        total, found = 0, False
        for message in result.get("messages") or []:
            metadata = message.get("response_metadata") or {}
            usage = metadata.get("token_usage") or metadata.get("usage") or {}
            if usage.get("total_tokens"):
                total += usage["total_tokens"]
                found = True
        return total if found else None

    @staticmethod
    def _preview(result: dict[str, Any], limit: int = 3900) -> str | None:
        for message in reversed(result.get("messages") or []):
            if message.get("type") != "ai":
                continue
            content = message.get("content")
            if isinstance(content, list):
                content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
            if isinstance(content, str) and content.strip():
                return content.strip()[:limit]
        return None

    async def latest_checkpoint(self, thread_id: str, *, client_factory=None) -> str | None:
        async with self._client(client_factory, 30) as client:
            response = await client.get(f"/api/threads/{thread_id}/state")
        if response.status_code != 200:
            return None
        checkpoint = response.json().get("checkpoint_id")
        return checkpoint if isinstance(checkpoint, str) and checkpoint else None

    async def list_model_profiles(self, *, client_factory=None) -> list[dict[str, Any]]:
        try:
            async with self._client(client_factory, 15) as client:
                response = await client.get("/api/models")
        except httpx.HTTPError as exc:
            raise RuntimeServiceError(503, "Gateway 模型目录暂不可用", kind="transient") from exc
        if response.status_code != 200:
            raise RuntimeServiceError(502, f"Gateway 模型目录返回 {response.status_code}")
        return [
            {
                "name": item["name"],
                "model": item["model"],
                "display_name": item.get("display_name") or item["name"],
                "description": item.get("description"),
                "supports_thinking": bool(item.get("supports_thinking")),
                "supports_reasoning_effort": bool(item.get("supports_reasoning_effort")),
            }
            for item in response.json().get("models") or []
        ]

    async def list_legacy_crons(self, *, client_factory=None) -> list[dict[str, Any]]:
        async with self._client(client_factory, 30) as client:
            response = await client.get("/api/scheduled-tasks")
        return response.json()

    async def pause_legacy_cron(self, cron_id: str, *, client_factory=None) -> bool:
        return await self._cron_action("pause", cron_id, client_factory)

    async def resume_legacy_cron(self, cron_id: str, *, client_factory=None) -> bool:
        return await self._cron_action("resume", cron_id, client_factory)

    async def _cron_action(self, action: str, cron_id: str, client_factory) -> bool:
        async with self._client(client_factory, 30) as client:
            response = await client.post(f"/api/scheduled-tasks/{cron_id}/{action}")
        return response.status_code == 200

    async def delete_legacy_cron(self, cron_id: str, *, client_factory=None) -> bool:
        async with self._client(client_factory, 30) as client:
            response = await client.delete(f"/api/scheduled-tasks/{cron_id}")
        return response.status_code in (200, 204)
