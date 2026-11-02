"""External execution semantics for third-party runners (FLEET-004).

Internal Loop states remain compatible. The API additionally distinguishes a
permanent failed attempt from queued retries and normal active execution.
"""

from __future__ import annotations

from typing import Any


def enrich_execution(item: dict[str, Any]) -> dict[str, Any]:
    status = item.get("latest_loop_status") or item.get("status")
    # Task 列表同时带 Task.error 与 Loop.error，优先展示最新 Loop 的错误。
    error = item["loop_error"] if "loop_error" in item else item.get("error")
    failure_kind = item.get("failure_kind")
    recovery = item.get("recovery_action")
    if status in ("delivered", "accepted"):
        execution_status = "succeeded"
    elif status == "archived":
        execution_status = "closed"
    elif status in ("error", "rejected"):
        execution_status = "failed"
    elif status == "running":
        execution_status = "running"
    elif error and not recovery:
        # 包括 transient/recursion 的预算耗尽：没有后续动作就是当前尝试终态。
        execution_status = "failed"
    else:
        execution_status = "queued"
    terminal = execution_status in ("succeeded", "failed", "closed")
    item.update({
        "execution_status": execution_status,
        "is_terminal": terminal,
        "retryable": (not terminal) and bool(recovery),
        "failure_kind": failure_kind,
        "last_error": error,
        "next_retry_at": item.get("next_retry_at"),
        "worker_state": (
            "terminal" if terminal else
            "running" if status == "running" else
            "retry_scheduled" if recovery else "queued"
        ),
    })
    return item
