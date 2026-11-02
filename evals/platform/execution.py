"""Shared, side-effect-explicit helpers for bounded platform evaluations.

No function in this module starts work on import. Network activity only occurs
through an explicitly constructed FleetClient and an explicit runner invocation.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


class ExecutionError(RuntimeError):
    """Execution evidence is missing, malformed, or inconsistent."""


class BudgetExceeded(ExecutionError):
    """A hard wall-clock or token budget has been exhausted."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_json(value: Any) -> str:
    blob = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def atomic_write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    temporary.replace(target)


def artifact_path(root: str | Path, candidate: str | Path) -> Path:
    """Resolve an artifact path without allowing traversal outside its root."""
    base = Path(root).resolve()
    raw = Path(candidate)
    resolved = (base / raw).resolve() if not raw.is_absolute() else raw.resolve()
    if resolved != base and base not in resolved.parents:
        raise ExecutionError(f"产物路径越界：{candidate}")
    return resolved


def parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        raise ExecutionError("交付不是 JSON 对象")
    decoder = json.JSONDecoder()
    for index, char in enumerate(value):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(value[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ExecutionError("交付不是可解析的 JSON 对象")


@dataclass
class ExecutionBudget:
    max_wall_seconds: float
    max_tokens: int
    clock: Callable[[], float] = time.monotonic
    started_monotonic: float | None = None
    total_tokens: int = 0
    stop_reason: str | None = None

    def __post_init__(self) -> None:
        if self.max_wall_seconds <= 0 or self.max_tokens <= 0:
            raise ValueError("预算上限必须为正数")
        if self.started_monotonic is None:
            self.started_monotonic = self.clock()

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, self.clock() - float(self.started_monotonic))

    def set_total_tokens(self, total: int) -> None:
        if total < 0:
            raise ValueError("Token 用量不能为负数")
        self.total_tokens = int(total)

    def check(self) -> None:
        if self.stop_reason:
            raise BudgetExceeded(self.stop_reason)
        if self.elapsed_seconds >= self.max_wall_seconds:
            self.stop_reason = "time_budget_exhausted"
        elif self.total_tokens >= self.max_tokens:
            self.stop_reason = "token_budget_exhausted"
        if self.stop_reason:
            raise BudgetExceeded(self.stop_reason)

    def snapshot(self) -> dict[str, Any]:
        return {
            "max_wall_seconds": self.max_wall_seconds,
            "elapsed_seconds": self.elapsed_seconds,
            "max_tokens": self.max_tokens,
            "total_tokens": self.total_tokens,
            "stop_reason": self.stop_reason,
        }


@dataclass
class HumanTimeLedger:
    events: list[dict[str, Any]] = field(default_factory=list)

    def add(self, kind: str, seconds: float, note: str = "") -> None:
        if not kind or seconds < 0:
            raise ValueError("人工事件需要 kind 和非负秒数")
        self.events.append({"kind": kind, "seconds": float(seconds), "note": note, "recorded_at": utc_now()})

    @property
    def total_seconds(self) -> float:
        return sum(float(event["seconds"]) for event in self.events)

    def count(self, kind: str) -> int:
        return sum(event["kind"] == kind for event in self.events)

    def snapshot(self) -> dict[str, Any]:
        return {
            "events": self.events,
            "active_human_seconds": self.total_seconds,
            "active_human_minutes": self.total_seconds / 60,
            "human_resume_count": self.count("human_resume"),
            "human_intervention_count": sum(
                event["kind"] not in {"maintainer_trigger", "independent_acceptance"}
                for event in self.events
            ),
        }


def vgh_measurement(accepted_goals: int, active_human_seconds: float) -> dict[str, Any]:
    if accepted_goals < 0 or active_human_seconds < 0:
        raise ValueError("Goal 数与人工时间不能为负数")
    result = {
        "accepted_goals": int(accepted_goals),
        "active_human_seconds": float(active_human_seconds),
        "active_human_minutes": float(active_human_seconds) / 60,
        "vgh": None,
        "measurement_status": "human_time_missing",
    }
    if active_human_seconds > 0:
        result["vgh"] = 3600 * accepted_goals / active_human_seconds
        result["measurement_status"] = "measured"
    return result


def require_execution_projection(item: dict[str, Any]) -> dict[str, Any]:
    """Require ADR-0038 fields; never infer terminal state from claimed+error."""
    status = item.get("execution_status")
    terminal = item.get("is_terminal")
    if status not in {"queued", "running", "succeeded", "failed", "closed"}:
        raise ExecutionError("缺少合法 execution_status；禁止按旧 Loop 状态猜终态")
    if not isinstance(terminal, bool):
        raise ExecutionError("缺少布尔 is_terminal")
    return {
        "execution_status": status,
        "is_terminal": terminal,
        "retryable": bool(item.get("retryable")),
        "failure_kind": item.get("failure_kind"),
        "last_error": item.get("last_error"),
        "worker_state": item.get("worker_state"),
        "next_retry_at": item.get("next_retry_at"),
    }


def latest_loop(detail: dict[str, Any]) -> dict[str, Any] | None:
    loops = detail.get("loops") or []
    if not loops:
        return None
    return max(loops, key=lambda loop: int(loop.get("id") or 0))


def delivery_from_detail(detail: dict[str, Any]) -> dict[str, Any]:
    loop = latest_loop(detail)
    if loop is None:
        return {"state": "pending", "deliverable": None, "loop": None}
    projection = require_execution_projection(loop)
    if projection["execution_status"] == "failed":
        return {"state": "failed", "deliverable": None, "loop": loop, "projection": projection}
    if projection["execution_status"] == "closed":
        return {"state": "closed", "deliverable": None, "loop": loop, "projection": projection}
    if projection["execution_status"] == "succeeded" and loop.get("deliverable"):
        return {"state": "succeeded", "deliverable": loop["deliverable"], "loop": loop, "projection": projection}
    return {"state": "pending", "deliverable": None, "loop": loop, "projection": projection}


def token_totals(usages: list[dict[str, Any]]) -> dict[str, int]:
    keys = ("total_tokens", "total_input_tokens", "total_output_tokens", "total_runs")
    return {key: sum(int(usage.get(key) or 0) for usage in usages) for key in keys}


class FleetClient:
    """Small FAP client. PAT is kept in memory and never written to artifacts."""

    def __init__(self, base_url: str, pat: str | None = None, timeout: float = 60):
        if not base_url:
            raise ValueError("需要 Fleet API 地址")
        self.base_url = base_url.rstrip("/")
        self.pat = pat or None
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode({key: value for key, value in query.items() if value is not None})
        payload = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        key = idempotency_key or (uuid.uuid4().hex if method.upper() not in {"GET", "HEAD"} else None)
        last_error: Exception | None = None
        for attempt in range(3):
            request = urllib.request.Request(url, data=payload, method=method.upper())
            request.add_header("Content-Type", "application/json")
            if self.pat:
                request.add_header("Authorization", f"Bearer {self.pat}")
            if key:
                request.add_header("Idempotency-Key", key)
                request.add_header("X-Client-Request-Id", key)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read().decode()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")[:1000]
                retry_after = float(exc.headers.get("Retry-After") or (15 if exc.code == 429 else 1))
                last_error = ExecutionError(f"Fleet HTTP {exc.code}: {detail}")
                if attempt < 2 and (exc.code == 429 or 500 <= exc.code < 600):
                    time.sleep(max(1, retry_after))
                    continue
                raise last_error from None
            except urllib.error.URLError as exc:
                last_error = ExecutionError(f"Fleet 连接失败：{exc.reason}")
                if attempt < 2:
                    time.sleep(2)
                    continue
                raise last_error from None
        raise last_error or ExecutionError("Fleet 请求失败")

    def get_agent(self, name: str) -> dict[str, Any] | None:
        try:
            return self.request("GET", f"/api/agents/{name}")
        except ExecutionError as exc:
            if "Fleet HTTP 404:" in str(exc):
                return None
            raise

    def put_agent(self, name: str, doc: dict[str, Any], key: str) -> Any:
        return self.request("PUT", f"/api/agents/{name}", body={"yaml": doc}, idempotency_key=key)

    def dispatch(self, agent: str, prompt: str, model_profile: str, key: str, actor: str) -> int:
        created = self.request(
            "POST",
            f"/api/agents/{agent}/dispatch",
            body={
                "input": prompt,
                "source": "agent",
                "actor": actor,
                "model_profile": model_profile,
                "subagent_model_policy": "auto",
                "idempotency_key": key,
            },
            idempotency_key=key,
        )
        task_id = created.get("task_id") or created.get("id")
        if not task_id:
            raise ExecutionError("Fleet 未返回 task_id")
        return int(task_id)

    def task_detail(self, task_id: int) -> dict[str, Any]:
        return self.request("GET", f"/api/tasks/{task_id}")

    def tasks(self, task_ids: list[int]) -> list[dict[str, Any]]:
        if not task_ids or len(task_ids) > 100:
            raise ValueError("批量读取需要 1–100 个 Task ID")
        return self.request("GET", "/api/tasks", query={"ids": ",".join(map(str, sorted(set(task_ids))))})

    def token_usage(self, thread_id: str) -> dict[str, Any]:
        return self.request("GET", f"/api/threads/{thread_id}/token-usage")

    def close_task(self, task_id: int, key: str, actor: str) -> Any:
        return self.request(
            "POST", f"/api/tasks/{task_id}/close", query={"author": actor}, idempotency_key=key)

    def create_report(self, payload: dict[str, Any], key: str) -> Any:
        return self.request("POST", "/api/eval/reports", body=payload, idempotency_key=key)
