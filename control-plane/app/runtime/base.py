"""Runtime-neutral Agent execution contracts (ADR-0032)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

TRANSIENT_HTTP_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}


def classify_runtime_failure(error: str, *, status_code: int | None = None,
                             transport: bool = False) -> str:
    if "recursion limit" in error.lower() or "graphrecursionerror" in error.lower():
        return "recursion"
    if transport or status_code in TRANSIENT_HTTP_STATUSES:
        return "transient"
    return "permanent"


class RuntimeServiceError(Exception):
    def __init__(self, status: int, message: str, *, kind: str = "permanent"):
        super().__init__(message)
        self.status = status
        self.kind = kind


@dataclass(frozen=True)
class RunRequest:
    agent_name: str
    thread_id: str
    loop_id: int
    input_text: str
    model_profile: str | None = None
    recovery_action: str | None = None
    checkpoint_ref: str | None = None
    recursion_limit: int = 300
    subagent_enabled: bool = True

    @property
    def resuming(self) -> bool:
        return self.recovery_action == "resume" and bool(self.checkpoint_ref)


@dataclass
class RunResult:
    status: str = "success"
    text: str | None = None
    tokens: int | None = None
    artifacts: list[str] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    run_id: str | None = None
    checkpoint_ref: str | None = None


@dataclass(frozen=True)
class RuntimeCapabilities:
    resume: bool
    stream: bool
    cancel: bool
    feedback: bool
    artifacts: bool
    subagents: bool
    managed_models: bool
    legacy_crons: bool = False


class RuntimeAdapter(ABC):
    name: str

    @abstractmethod
    def capabilities(self) -> RuntimeCapabilities:
        raise NotImplementedError

    @abstractmethod
    def agent_package(self, document: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def deploy_agent(self, name: str, document: dict[str, Any], *, client_factory=None) -> None:
        raise NotImplementedError

    @abstractmethod
    async def remove_agent(self, name: str, *, client_factory=None) -> None:
        raise NotImplementedError

    @abstractmethod
    async def execute_run(self, request: RunRequest, *, client_factory=None,
                          recovery_instruction: str = "") -> RunResult:
        raise NotImplementedError

    async def resume_run(self, request: RunRequest, *, client_factory=None,
                         recovery_instruction: str = "") -> RunResult:
        if not self.capabilities().resume:
            raise RuntimeServiceError(409, f"Runtime '{self.name}' 不支持 checkpoint resume", kind="capability")
        return await self.execute_run(
            request, client_factory=client_factory,
            recovery_instruction=recovery_instruction,
        )

    @abstractmethod
    async def latest_checkpoint(self, thread_id: str, *, client_factory=None) -> str | None:
        raise NotImplementedError

    @abstractmethod
    async def list_model_profiles(self, *, client_factory=None) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def list_legacy_crons(self, *, client_factory=None) -> list[dict[str, Any]]:
        raise RuntimeServiceError(409, f"Runtime '{self.name}' 不支持旧网关 cron", kind="capability")

    async def pause_legacy_cron(self, cron_id: str, *, client_factory=None) -> bool:
        raise RuntimeServiceError(409, f"Runtime '{self.name}' 不支持旧网关 cron", kind="capability")

    async def resume_legacy_cron(self, cron_id: str, *, client_factory=None) -> bool:
        raise RuntimeServiceError(409, f"Runtime '{self.name}' 不支持旧网关 cron", kind="capability")

    async def delete_legacy_cron(self, cron_id: str, *, client_factory=None) -> bool:
        raise RuntimeServiceError(409, f"Runtime '{self.name}' 不支持旧网关 cron", kind="capability")
