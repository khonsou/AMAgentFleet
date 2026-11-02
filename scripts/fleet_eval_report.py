#!/usr/bin/env python3
"""Build and persist the first real platform evaluation from Task 318–320 evidence.

No model is called. Raw evidence stays in the local redacted artifact; only the
scorecard and proposed recommendations are sent to the Control Plane.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "artifacts/fleet-autonomous-context-summary.json"
CP = "http://127.0.0.1:8003"


def build_measurement(evidence: dict, platform_version: str) -> dict:
    autonomy = evidence["autonomy"]
    tokens = evidence["token_usage"]["total"]
    security_failed = bool(evidence.get("security_findings"))
    return {
        "platform_version": platform_version,
        "scenario_version": "core-v1",
        "gates": {
            "P0": not autonomy["required_external_resume"],
            "P1": not security_failed,
            "P2": False,
        },
        "dimensions": {
            "task_goal": 2,
            "multi_agent": 4,
            "protocol": 4,
            "runtime": 3,
            "evidence_auth": 1 if security_failed else 4,
            "playground": 0,
            "engineering_cost": 0,
        },
        "baseline": {
            "comparable": False,
            "reason": "No same-condition single-agent or previous-release baseline",
        },
        "repeatability": {
            "runtime_types": 1,
            "third_party_agents": 1,
            "consecutive_stable_runs": 0,
        },
        "progress": {
            "no_improvement_rounds": 0,
            "repeated_failure": False,
            "experiments": 1,
            "tokens": tokens["total_tokens"],
            "minutes": evidence["elapsed_seconds"] / 60,
            "no_new_hypothesis": False,
            "maintainer_stop": False,
        },
    }


def build_recommendations() -> list[dict]:
    task_ids = [318, 319, 320]
    return [
        {
            "dimension": "task_goal",
            "summary": "实现子 Task 交付后自动唤醒父 Thread；复测时禁止人工发送继续整合指令。",
            "evidence": {"task_ids": task_ids, "issue": "required_external_resume"},
        },
        {
            "dimension": "evidence_auth",
            "summary": "将源系统凭据统一收敛到 Gateway credential handle，清理本次子 Task 输入中的秘密正文并复核记忆提取边界。",
            "evidence": {"task_ids": task_ids, "issue": "secret_text_copied_to_task_input"},
        },
        {
            "dimension": "playground",
            "summary": "由非工程用户执行注册、赋予 Goal、启动、观察证据和异常处理的核心可用性验收。",
            "evidence": {"issue": "non_engineer_core_path_not_evaluated"},
        },
        {
            "dimension": "engineering_cost",
            "summary": "为子任务增加结构化摘要、确定性数值校验和 per-child 检索预算，并与单 Agent 技术基线比较。",
            "evidence": {"task_ids": task_ids, "issue": "high_token_and_unbounded_summary"},
        },
    ]


async def _activate_directly(payload: dict) -> dict:
    from app import evaluation, registry
    from app.db import init_db

    await init_db()
    await registry.ensure_eval_agent()
    body = evaluation.ReportInput(**payload)
    request = type("Request", (), {
        "state": type("State", (), {"fap_actor": "scripts/fleet_eval_report.py"})(),
    })()
    return await evaluation.create_report(body, request)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--direct", action="store_true", help="Activate through Control Plane functions without restarting services")
    args = parser.parse_args()
    evidence = json.loads(EVIDENCE.read_text())
    platform_version = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    payload = {
        "scenario_id": "core-v1",
        "measurement": build_measurement(evidence, platform_version),
        "recommendations": build_recommendations(),
    }
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.direct:
        result = asyncio.run(_activate_directly(payload))
    else:
        request = urllib.request.Request(
            f"{CP}/api/eval/reports",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": f"eval:task-318:{platform_version}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.load(response)
    output = ROOT / "artifacts/platform-evals/core-v1/task-318/report-response.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print(f"ARTIFACT={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
