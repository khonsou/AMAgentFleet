"""DeerFlow/LangGraph implementation of the Fleet RuntimeAdapter."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from ..db import GATEWAY, pool as db_pool
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

    @staticmethod
    def _ser(row) -> dict[str, Any]:
        value = dict(row)
        for key, item in value.items():
            if isinstance(item, datetime):
                value[key] = item.astimezone(timezone.utc).isoformat()
        return value

    async def get_overview(self, *, client_factory=None) -> dict[str, Any]:
        async with (await db_pool()).acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                  (SELECT count(*) FROM threads_meta) AS threads,
                  (SELECT count(*) FROM runs) AS runs,
                  (SELECT count(*) FROM runs WHERE status = 'running') AS running,
                  (SELECT count(*) FROM runs WHERE status = 'error') AS errored,
                  (SELECT coalesce(sum(total_tokens), 0) FROM runs
                    WHERE created_at::date = current_date) AS tokens_today,
                  (SELECT coalesce(sum(total_tokens), 0) FROM runs) AS tokens_total,
                  (SELECT count(*) FROM checkpoints) AS checkpoints
                """
            )
        return self._ser(row)

    async def list_threads(self, *, client_factory=None) -> list[dict[str, Any]]:
        async with (await db_pool()).acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                  t.thread_id, t.display_name, t.assistant_id,
                  t.status AS thread_status, t.created_at, t.updated_at,
                  COALESCE(s.run_count, 0) AS run_count,
                  COALESCE(s.total_tokens, 0) AS total_tokens,
                  COALESCE(s.total_input, 0) AS total_input_tokens,
                  COALESCE(s.total_output, 0) AS total_output_tokens,
                  COALESCE(s.llm_calls, 0) AS llm_call_count,
                  s.last_run_status, s.last_run_at, s.first_message,
                  COALESCE(c.checkpoint_count, 0) AS checkpoint_count
                FROM threads_meta t
                LEFT JOIN LATERAL (
                  SELECT count(*) AS run_count,
                    sum(r.total_tokens) AS total_tokens,
                    sum(r.total_input_tokens) AS total_input,
                    sum(r.total_output_tokens) AS total_output,
                    sum(r.llm_call_count) AS llm_calls,
                    (SELECT r2.status FROM runs r2
                      WHERE r2.thread_id = t.thread_id
                      ORDER BY r2.created_at DESC LIMIT 1) AS last_run_status,
                    max(r.updated_at) AS last_run_at,
                    (SELECT r3.first_human_message FROM runs r3
                      WHERE r3.thread_id = t.thread_id
                      ORDER BY r3.created_at ASC LIMIT 1) AS first_message
                  FROM runs r WHERE r.thread_id = t.thread_id
                ) s ON true
                LEFT JOIN LATERAL (
                  SELECT count(*) AS checkpoint_count
                  FROM checkpoints c WHERE c.thread_id = t.thread_id
                ) c ON true
                ORDER BY t.updated_at DESC
                """
            )
        return [self._ser(row) for row in rows]

    async def get_thread(self, thread_id: str, *, client_factory=None) -> dict[str, Any]:
        async with (await db_pool()).acquire() as conn:
            thread = await conn.fetchrow("SELECT * FROM threads_meta WHERE thread_id=$1", thread_id)
            if not thread:
                raise RuntimeServiceError(404, "thread not found")
            runs = await conn.fetch(
                """SELECT run_id, status, model_name, created_at, updated_at,
                       total_input_tokens, total_output_tokens, total_tokens,
                       llm_call_count, message_count, error, stop_reason,
                       first_human_message, last_ai_message
                   FROM runs WHERE thread_id=$1 ORDER BY created_at DESC""",
                thread_id,
            )
            checkpoints = await conn.fetchval(
                "SELECT count(*) FROM checkpoints WHERE thread_id=$1", thread_id)
        return {
            "thread": self._ser(thread),
            "runs": [self._ser(run) for run in runs],
            "checkpoints": checkpoints,
        }

    async def _json_request(self, method: str, path: str, *, client_factory=None,
                            query: dict[str, Any] | None = None, body=None,
                            timeout: float = 30) -> Any:
        async with self._client(client_factory, timeout) as client:
            response = await client.request(method, path, params=query, json=body)
        if response.status_code >= 400:
            raise RuntimeServiceError(response.status_code, response.text[:500])
        return response.json() if response.text else None

    async def get_messages(self, thread_id: str, *, client_factory=None) -> Any:
        return await self._json_request("GET", f"/api/threads/{thread_id}/messages", client_factory=client_factory)

    async def create_thread(self, assistant_id: str, instruction: str | None = None,
                            recursion_limit: int = 300, *, client_factory=None) -> dict[str, Any]:
        async with self._client(client_factory, 30) as client:
            response = await client.post("/api/threads", json={"assistant_id": assistant_id})
        if response.status_code != 200:
            raise RuntimeServiceError(response.status_code, response.text[:500])
        thread_id = response.json()["thread_id"]
        if instruction and instruction.strip():
            await self.execute_run(RunRequest(
                agent_name=assistant_id, thread_id=thread_id, loop_id=0,
                input_text=instruction.strip(), recursion_limit=recursion_limit,
            ), client_factory=client_factory)
        return {"thread_id": thread_id}

    async def send_message(self, thread_id: str, content: str, assistant_id: str | None = None,
                           recursion_limit: int = 300, *, client_factory=None) -> dict[str, Any]:
        async with (await db_pool()).acquire() as conn:
            stored = await conn.fetchval(
                "SELECT assistant_id FROM threads_meta WHERE thread_id=$1", thread_id)
        if assistant_id and stored and stored != assistant_id:
            raise RuntimeServiceError(409, "thread assistant_id conflicts with requested assistant_id")
        agent = assistant_id or stored or "lead_agent"
        result = await self.execute_run(RunRequest(
            agent_name=agent, thread_id=thread_id, loop_id=0,
            input_text=content, recursion_limit=recursion_limit,
        ), client_factory=client_factory)
        return {
            "status": result.status, "messages": result.messages,
            "run_id": result.run_id, "checkpoint_id": result.checkpoint_ref,
        }

    async def delete_thread(self, thread_id: str, *, client_factory=None) -> dict[str, Any]:
        await self._json_request("DELETE", f"/api/threads/{thread_id}", client_factory=client_factory)
        return {"deleted": thread_id}

    async def get_thread_state(self, thread_id: str, *, client_factory=None) -> dict[str, Any]:
        return await self._json_request("GET", f"/api/threads/{thread_id}/state", client_factory=client_factory)

    async def get_token_usage(self, thread_id: str, *, client_factory=None) -> Any:
        return await self._json_request("GET", f"/api/threads/{thread_id}/token-usage", client_factory=client_factory)

    async def get_run(self, thread_id: str, run_id: str, *, client_factory=None) -> Any:
        return await self._json_request("GET", f"/api/threads/{thread_id}/runs/{run_id}", client_factory=client_factory)

    async def get_run_messages(self, run_id: str, query: dict[str, Any], *, client_factory=None) -> Any:
        return await self._json_request("GET", f"/api/runs/{run_id}/messages", client_factory=client_factory, query=query)

    async def get_run_events(self, thread_id: str, run_id: str, *, client_factory=None) -> Any:
        return await self._json_request("GET", f"/api/threads/{thread_id}/runs/{run_id}/events", client_factory=client_factory)

    async def cancel_run(self, thread_id: str, run_id: str, *, client_factory=None) -> Any:
        return await self._json_request("POST", f"/api/threads/{thread_id}/runs/{run_id}/cancel", client_factory=client_factory)

    async def feedback(self, method: str, thread_id: str, run_id: str, *,
                       body=None, feedback_id: str | None = None, client_factory=None) -> Any:
        suffix = f"/{feedback_id}" if feedback_id else ""
        return await self._json_request(
            method, f"/api/threads/{thread_id}/runs/{run_id}/feedback{suffix}",
            client_factory=client_factory, body=body,
        )

    async def get_artifact(self, thread_id: str, path: str, *, client_factory=None) -> tuple[bytes, str]:
        async with self._client(client_factory, 60) as client:
            response = await client.get(f"/api/threads/{thread_id}/artifacts/{path}")
        if response.status_code != 200:
            raise RuntimeServiceError(response.status_code, response.text[:500])
        return response.content, response.headers.get("content-type", "application/octet-stream")
