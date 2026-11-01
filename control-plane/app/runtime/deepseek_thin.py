"""DeepSeek OpenAI-compatible thin Runner reference implementation (test only).

No proprietary SDK, checkpoints, hidden tool execution or arbitrary egress. State is
process-local by design; production migration and checkpoint compatibility are out of scope.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from .base import (
    RunRequest, RunResult, RuntimeAdapter, RuntimeCapabilities,
    RuntimeServiceError, classify_runtime_failure,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DeepSeekThinAdapter(RuntimeAdapter):
    name = "deepseek-thin"

    def __init__(self, base_url: str | None = None, model: str | None = None):
        self.base_url = (base_url or os.environ.get("DEEPSEEK_THIN_BASE_URL", "https://api.deepseek.com")).rstrip("/")
        self.model = model or os.environ.get("DEEPSEEK_THIN_MODEL", "deepseek-flash")
        self._agents: dict[str, dict[str, Any]] = {}
        self._threads: dict[str, list[dict[str, Any]]] = {}
        self._thread_agents: dict[str, str] = {}
        self._runs: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    def _client(self, factory, timeout: float = 600):
        return (factory or httpx.AsyncClient)(base_url=self.base_url, timeout=timeout, trust_env=False)

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            resume=False, stream=False, cancel=False, feedback=False,
            artifacts=False, subagents=False, managed_models=False, legacy_crons=False,
        )

    def agent_package(self, document: dict[str, Any]) -> dict[str, Any]:
        if document.get("apiVersion") != "agentfleet/v1":
            raise RuntimeServiceError(422, "thin runtime requires apiVersion agentfleet/v1")
        spec = document.get("spec") or {}
        agent = spec.get("agent") or {}
        if not isinstance(agent, dict) or not agent.get("name"):
            raise RuntimeServiceError(422, "thin runtime requires spec.agent.name")
        return {
            "format": "agentfleet.thin-agent", "version": 1,
            "agent": {
                "name": agent["name"],
                "display_name": agent.get("display_name") or agent["name"],
                "description": agent.get("description") or "",
                "soul": agent.get("soul") or agent.get("description") or "",
                "model": agent.get("model"),
                "model_settings": agent.get("model_settings") or {},
            },
        }

    async def deploy_agent(self, name: str, document: dict[str, Any], *, client_factory=None) -> None:
        package = self.agent_package(document)
        async with self._lock:
            self._agents[name] = package["agent"]

    async def remove_agent(self, name: str, *, client_factory=None) -> None:
        async with self._lock:
            self._agents.pop(name, None)

    def _new_thread(self, agent_name: str, thread_id: str) -> None:
        self._threads.setdefault(thread_id, [])
        self._thread_agents.setdefault(thread_id, agent_name)

    async def execute_run(self, request: RunRequest, *, client_factory=None,
                          recovery_instruction: str = "") -> RunResult:
        if request.resuming:
            raise RuntimeServiceError(
                409, "capability_not_supported: deepseek-thin.resume", kind="capability")
        agent = self._agents.get(request.agent_name)
        if agent is None:
            raise RuntimeServiceError(404, f"thin runtime agent not deployed: {request.agent_name}")
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeServiceError(503, "DEEPSEEK_API_KEY 未配置")
        self._new_thread(request.agent_name, request.thread_id)
        history = list(self._threads[request.thread_id])
        user_message = {
            "type": "human", "role": "user", "content": request.input_text,
            "created_at": _now(),
        }
        selected_profile = request.model_profile or agent.get("model") or self.model
        model = self.model if selected_profile == "deepseek-default" else selected_profile
        settings = agent.get("model_settings") or {}
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": agent.get("soul") or "You are a helpful agent."},
                *[{"role": item["role"], "content": item["content"]} for item in history],
                {"role": "user", "content": request.input_text},
            ],
        }
        if isinstance(settings.get("temperature"), (int, float)):
            body["temperature"] = settings["temperature"]
        try:
            async with self._client(client_factory) as client:
                response = await client.post(
                    "/chat/completions", json=body,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
        except httpx.HTTPError as exc:
            raise RuntimeServiceError(503, f"DeepSeek 连接失败: {exc}", kind="transient") from exc
        if response.status_code != 200:
            message = f"DeepSeek {response.status_code}: {response.text[:300]}"
            raise RuntimeServiceError(
                response.status_code, message,
                kind=classify_runtime_failure(message, status_code=response.status_code),
            )
        payload = response.json()
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeServiceError(502, "DeepSeek 响应缺少 choices[0].message.content") from exc
        if isinstance(content, list):
            content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
        usage = payload.get("usage") or {}
        run_id = f"thin-run-{uuid.uuid4()}"
        assistant_message = {
            "type": "ai", "role": "assistant", "content": str(content),
            "run_id": run_id, "created_at": _now(),
            "response_metadata": {"token_usage": usage},
        }
        user_message["run_id"] = run_id
        run = {
            "run_id": run_id, "thread_id": request.thread_id, "status": "success",
            "model_name": model, "created_at": assistant_message["created_at"],
            "updated_at": assistant_message["created_at"],
            "total_input_tokens": usage.get("prompt_tokens", 0),
            "total_output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "llm_call_count": 1, "message_count": 2,
            "error": None, "stop_reason": "stop",
            "first_human_message": request.input_text,
            "last_ai_message": str(content),
            "messages": [user_message, assistant_message],
        }
        async with self._lock:
            self._threads[request.thread_id].extend([user_message, assistant_message])
            self._runs.append(run)
        return RunResult(
            text=str(content), tokens=usage.get("total_tokens"),
            messages=[user_message, assistant_message], run_id=run_id,
        )

    async def latest_checkpoint(self, thread_id: str, *, client_factory=None) -> str | None:
        return None

    async def list_model_profiles(self, *, client_factory=None) -> list[dict[str, Any]]:
        return [{
            "name": "deepseek-default", "model": self.model,
            "display_name": "DeepSeek Thin",
            "description": "OpenAI-compatible thin Runner",
            "supports_thinking": False, "supports_reasoning_effort": False,
        }]

    async def get_overview(self, *, client_factory=None) -> dict[str, Any]:
        return {
            "threads": len(self._threads), "runs": len(self._runs),
            "running": 0, "errored": 0,
            "tokens_today": sum(run["total_tokens"] for run in self._runs),
            "tokens_total": sum(run["total_tokens"] for run in self._runs),
            "checkpoints": 0,
        }

    def _thread_row(self, thread_id: str) -> dict[str, Any]:
        runs = [run for run in self._runs if run["thread_id"] == thread_id]
        messages = self._threads[thread_id]
        latest = runs[-1] if runs else None
        return {
            "thread_id": thread_id, "display_name": self._thread_agents[thread_id],
            "assistant_id": self._thread_agents[thread_id],
            "thread_status": "idle", "created_at": messages[0]["created_at"] if messages else _now(),
            "updated_at": messages[-1]["created_at"] if messages else _now(),
            "run_count": len(runs),
            "total_tokens": sum(run["total_tokens"] for run in runs),
            "total_input_tokens": sum(run["total_input_tokens"] for run in runs),
            "total_output_tokens": sum(run["total_output_tokens"] for run in runs),
            "llm_call_count": len(runs),
            "last_run_status": latest["status"] if latest else None,
            "last_run_at": latest["updated_at"] if latest else None,
            "first_message": messages[0]["content"] if messages else None,
            "checkpoint_count": 0,
        }

    async def list_threads(self, *, client_factory=None) -> list[dict[str, Any]]:
        return [self._thread_row(thread_id) for thread_id in self._threads]

    async def get_thread(self, thread_id: str, *, client_factory=None) -> dict[str, Any]:
        if thread_id not in self._threads:
            raise RuntimeServiceError(404, "thread not found")
        runs = [{
            key: value for key, value in run.items() if key != "messages"
        } for run in reversed(self._runs) if run["thread_id"] == thread_id]
        return {"thread": self._thread_row(thread_id), "runs": runs, "checkpoints": 0}

    async def get_messages(self, thread_id: str, *, client_factory=None) -> Any:
        if thread_id not in self._threads:
            raise RuntimeServiceError(404, "thread not found")
        return self._threads[thread_id]

    async def create_thread(self, assistant_id: str, instruction: str | None = None,
                            recursion_limit: int = 300, *, client_factory=None) -> dict[str, Any]:
        if assistant_id not in self._agents:
            raise RuntimeServiceError(404, f"thin runtime agent not deployed: {assistant_id}")
        thread_id = str(uuid.uuid4())
        self._new_thread(assistant_id, thread_id)
        if instruction and instruction.strip():
            await self.execute_run(RunRequest(
                agent_name=assistant_id, thread_id=thread_id, loop_id=0,
                input_text=instruction.strip(), recursion_limit=recursion_limit,
            ), client_factory=client_factory)
        return {"thread_id": thread_id}

    async def send_message(self, thread_id: str, content: str, assistant_id: str | None = None,
                           recursion_limit: int = 300, *, client_factory=None) -> dict[str, Any]:
        if thread_id not in self._threads:
            raise RuntimeServiceError(404, "thread not found")
        stored = self._thread_agents[thread_id]
        if assistant_id and stored != assistant_id:
            raise RuntimeServiceError(409, "thread assistant_id conflicts with requested assistant_id")
        result = await self.execute_run(RunRequest(
            agent_name=stored, thread_id=thread_id, loop_id=0,
            input_text=content, recursion_limit=recursion_limit,
        ), client_factory=client_factory)
        return {"status": result.status, "messages": result.messages, "run_id": result.run_id, "checkpoint_id": None}

    async def delete_thread(self, thread_id: str, *, client_factory=None) -> dict[str, Any]:
        self._threads.pop(thread_id, None)
        self._thread_agents.pop(thread_id, None)
        self._runs = [run for run in self._runs if run["thread_id"] != thread_id]
        return {"deleted": thread_id}

    async def get_thread_state(self, thread_id: str, *, client_factory=None) -> dict[str, Any]:
        if thread_id not in self._threads:
            raise RuntimeServiceError(404, "thread not found")
        return {"thread_id": thread_id, "checkpoint_id": None, "status": "idle"}

    async def get_token_usage(self, thread_id: str, *, client_factory=None) -> Any:
        return {"total_tokens": sum(run["total_tokens"] for run in self._runs if run["thread_id"] == thread_id)}

    async def get_run(self, thread_id: str, run_id: str, *, client_factory=None) -> Any:
        for run in self._runs:
            if run["thread_id"] == thread_id and run["run_id"] == run_id:
                return {key: value for key, value in run.items() if key != "messages"}
        raise RuntimeServiceError(404, "run not found")

    async def get_run_messages(self, run_id: str, query: dict[str, Any], *, client_factory=None) -> Any:
        for run in self._runs:
            if run["run_id"] == run_id:
                return run["messages"][:int(query.get("limit") or 50)]
        raise RuntimeServiceError(404, "run not found")

    async def get_run_events(self, thread_id: str, run_id: str, *, client_factory=None) -> Any:
        run = await self.get_run(thread_id, run_id)
        return [{"type": "run_completed", "run_id": run_id, "status": run["status"], "created_at": run["updated_at"]}]
