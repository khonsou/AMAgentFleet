#!/usr/bin/env python3
"""Persist the post-implementation scorecard using the successful auto-resume evidence."""

from __future__ import annotations

import asyncio
import json
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "artifacts/platform-evals/core-v1/auto-resume/result.json"
OUT = ROOT / "artifacts/platform-evals/core-v1/auto-resume/report-response.json"


def token_usage(thread_id: str) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:8003/api/threads/{thread_id}/token-usage", timeout=30) as response:
        return json.load(response)


def measurement(result: dict, platform_version: str) -> dict:
    usages = [token_usage(result["root"]["latest_thread_id"])]
    usages += [token_usage(child["latest_thread_id"]) for child in result["children"]]
    totals = {
        key: sum(item[key] for item in usages)
        for key in ("total_tokens", "total_input_tokens", "total_output_tokens", "total_runs")
    }
    return {
        "platform_version": platform_version,
        "scenario_version": "core-v1",
        "gates": {"P0": True, "P1": False, "P2": False},
        "dimensions": {
            "task_goal": 4,
            "multi_agent": 4,
            "protocol": 4,
            "runtime": 3,
            "evidence_auth": 2,
            "playground": 0,
            "engineering_cost": 2,
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
            "tokens": totals["total_tokens"],
            "minutes": result["elapsed_seconds"] / 60,
            "no_new_hypothesis": False,
            "maintainer_stop": False,
        },
        "evidence_refs": {
            "root_task_id": result["root_task_id"],
            "child_task_ids": [child["id"] for child in result["children"]],
            "token_usage": totals,
        },
    }


def recommendations() -> list[dict]:
    return [
        {
            "dimension": "evidence_auth",
            "summary": "复核现有 YAML 授权与记忆提取的秘密边界，不再新增 Timeline 专用代理或激活流程。",
            "evidence": {"issue": "secret_text_copied_to_task_input"},
        },
        {
            "dimension": "engineering_cost",
            "summary": "在相同受控面板下增加单 Agent 或上一版本基线，比较自动唤醒后的质量、Token 和人工介入。",
            "evidence": {"issue": "no_comparable_cost_baseline"},
        },
    ]


async def main() -> None:
    from app import evaluation

    result = json.loads(RESULT.read_text())
    if result.get("status") != "finished":
        raise SystemExit("Auto-resume acceptance has not finished successfully")
    platform_version = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    payload = {
        "scenario_id": "core-v1",
        "measurement": measurement(result, platform_version),
        "recommendations": recommendations(),
    }
    request = type("Request", (), {
        "state": type("State", (), {"fap_actor": "scripts/fleet_eval_auto_resume_report.py"})(),
    })()
    response = await evaluation.create_report(evaluation.ReportInput(**payload), request)
    OUT.write_text(json.dumps(response, ensure_ascii=False, indent=2, default=str) + "\n")
    print(json.dumps(response, ensure_ascii=False, indent=2, default=str))
    print(f"ARTIFACT={OUT}")


if __name__ == "__main__":
    asyncio.run(main())
