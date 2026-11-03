#!/usr/bin/env python3
"""Repeatable core-v1 single-agent control and multi-agent candidate runner.

The ``validate`` command is offline. Real work only starts through an explicit
``run`` command or the maintainer-only Control Plane evaluation endpoint.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import yaml

from evals.platform.execution import (
    BudgetExceeded,
    ExecutionBudget,
    ExecutionError,
    FleetClient,
    HumanTimeLedger,
    artifact_path,
    atomic_write_json,
    delivery_from_detail,
    latest_loop,
    parse_json_object,
    read_json,
    require_execution_projection,
    sha256_json,
    token_totals,
    utc_now,
    vgh_measurement,
)
from evals.platform.north_star import SZ_VSR, zero_human_verified_success
from evals.platform.scorecard import DIMENSIONS, calculate_scorecard, load_scenario

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO = ROOT / "evals/platform/core-v1/scenario.yaml"
ARTIFACT_ROOT = ROOT / "artifacts/platform-evals/core-v1/runs"
EVAL_AGENT = "fleet-eval-01"
AGENT_FILES = {
    "fleet-eval-source-a-01": "fleet-eval-source-a-01.yaml",
    "fleet-eval-source-b-01": "fleet-eval-source-b-01.yaml",
    "fleet-eval-single-01": "fleet-eval-single-01.yaml",
}
SOURCE_OPERATIONS = {
    "fleet_status": "eval.fixture.fleet_status",
    "timeline_boards": "eval.fixture.timeline_boards",
}
RECOMMENDATION_TEXT = {
    "task_goal": "改进 Task 依赖、自动唤醒、恢复或停止证据，并用同一 core-v1 面板复测。",
    "multi_agent": "改进真实 Agent 分工、 OUTPUT 组装或独立核验，不以预置输出替代协作。",
    "protocol": "补齐统一协议、结构化执行状态、幂等或错误证据。",
    "runtime": "补齐 Runtime Contract、Token／恢复观测或能力不支持证据。",
    "evidence_auth": "补齐来源、版本、授权和秘密边界证据。",
    "playground": "由非工程用户完成核心路径验收，记录主动人工时间与失败点。",
    "engineering_cost": "在相同单 Agent 对照下改善 VGH、Token、墙钟或人工介入。",
}


def current_platform_version() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def _fixture_path(scenario_dir: Path, scenario: dict[str, Any], key: str) -> Path:
    if key == "expected_facts":
        return scenario_dir / scenario["expected_facts"]
    return scenario_dir / scenario["fixtures"][key]


def load_core_inputs(scenario_path: Path) -> dict[str, Any]:
    scenario_path = scenario_path.resolve()
    scenario_dir = scenario_path.parent
    scenario = load_scenario(scenario_path)
    if scenario["id"] != "core-v1":
        raise ExecutionError("启动器只接受 core-v1")
    runner = scenario.get("runner") or {}
    protocol = scenario.get("measurement_protocol") or {}
    if not runner or not protocol:
        raise ExecutionError("core-v1 缺少 runner 或 measurement_protocol")
    fleet = read_json(_fixture_path(scenario_dir, scenario, "fleet_status"))
    timeline = read_json(_fixture_path(scenario_dir, scenario, "timeline_boards"))
    expected = read_json(_fixture_path(scenario_dir, scenario, "expected_facts"))
    return {
        "scenario": scenario,
        "scenario_path": scenario_path,
        "fleet": fleet,
        "timeline": timeline,
        "expected": expected,
        "fixtures_sha256": sha256_json({"fleet_status": fleet, "timeline_boards": timeline}),
    }


def output_contract() -> str:
    return """最终只输出一个 JSON 对象，结构如下：
{
  "fleet_pool_counts": {"open": <整数>, "review": <整数>, "done": <整数>},
  "timeline": {
    "board_count": <整数>, "card_count": <整数>,
    "status_counts": {"待执行": <整数>, "待发布": <整数>, "已发布": <整数>}
  },
  "sources": [
    {"operation_id": "eval.fixture.fleet_status", "captured_at": "<Fleet fixture 时间>"},
    {"operation_id": "eval.fixture.timeline_boards", "captured_at": "<Timeline fixture 时间>"}
  ],
  "summary": "<不超过 200 字的交叉结论>"
}
不要输出 Markdown 代码围栏，不要添加无来源数字。"""


def build_prompt(inputs: dict[str, Any], mode: str) -> str:
    scenario = inputs["scenario"]
    runner = scenario["runner"]
    facts = json.dumps(
        {"fleet_status": inputs["fleet"], "timeline_boards": inputs["timeline"]},
        ensure_ascii=False,
    )
    common = f"""【core-v1 受控双源事实简报｜{scenario['runner']['prompt_version']}】
只能使用下列受控 fixture，禁止联网、读取其他文件、索取凭据或使用外部数据：
{facts}

{output_contract()}
"""
    if mode == "single":
        return common + "独立完成全部计算与简报；不得调用工具、派生子任务或请求人工继续。"
    if mode == "multi":
        return common + f"""
你是多 Agent 候选工作流的指挥官，必须真实验证协作链：
1. 并行创建两个不同子 Task，分别指派 {runner['workers']['fleet_status']} 和 {runner['workers']['timeline_boards']}；每个子任务只携带对应 fixture 和明确 JSON 交付要求。
2. 子任务创建后先交付简短阶段性说明并结束当前回合；不得由用户再次发送继续。
3. 系统唤醒后读取真实子任务 OUTPUT，核对计算并组装上述最终 JSON；不得重复派发，不得把已创建当作已交付。
4. 不验收、不关闭、不修改任何既有任务，不读取任何预期答案或评测专用材料。"""
    raise ValueError("mode 必须为 single 或 multi")


def validate_delivery(value: Any, expected: dict[str, Any], fixtures: dict[str, Any]) -> dict[str, bool]:
    try:
        delivery = parse_json_object(value)
    except ExecutionError:
        return {
            "json_object": False,
            "fleet_pool_counts": False,
            "timeline_facts": False,
            "source_operations": False,
            "source_captured_at": False,
        }
    timeline = delivery.get("timeline") or {}
    sources = delivery.get("sources") if isinstance(delivery.get("sources"), list) else []
    by_operation = {
        item.get("operation_id"): item
        for item in sources
        if isinstance(item, dict) and item.get("operation_id")
    }
    expected_timeline = {
        "board_count": expected["board_count"],
        "card_count": expected["card_count"],
        "status_counts": expected["status_counts"],
    }
    return {
        "json_object": True,
        "fleet_pool_counts": delivery.get("fleet_pool_counts") == expected["fleet_pool_counts"],
        "timeline_facts": all(timeline.get(key) == val for key, val in expected_timeline.items()),
        "source_operations": all(operation in by_operation for operation in SOURCE_OPERATIONS.values()),
        "source_captured_at": all(
            (by_operation.get(SOURCE_OPERATIONS[key]) or {}).get("captured_at") == fixtures[key]["captured_at"]
            for key in SOURCE_OPERATIONS
        ),
    }


def _thread_ids(items: list[dict[str, Any]], detail: dict[str, Any]) -> set[str]:
    threads = {
        item["latest_thread_id"]
        for item in items
        if item.get("latest_thread_id")
    }
    for loop in detail.get("loops") or []:
        if loop.get("thread_id"):
            threads.add(loop["thread_id"])
    for item in items:
        for claimant in item.get("claimants") or []:
            if claimant.get("thread_id"):
                threads.add(claimant["thread_id"])
    return threads


def _collect_tokens(client: FleetClient, thread_ids: set[str]) -> dict[str, int]:
    return token_totals([client.token_usage(thread_id) for thread_id in sorted(thread_ids)])


def _task_status_signature(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    signature = []
    for item in sorted(items, key=lambda entry: entry["id"]):
        projection = require_execution_projection(item)
        signature.append({
            "task_id": item["id"],
            "agent_name": item.get("agent_name"),
            "latest_loop_id": item.get("latest_loop_id"),
            **projection,
        })
    return signature


def _prepare_agents(client: FleetClient, run_id: str) -> None:
    for name, filename in AGENT_FILES.items():
        doc = yaml.safe_load((ROOT / "control-plane/agents" / filename).read_text(encoding="utf-8"))
        current = client.get_agent(name)
        if current and current.get("yaml") == doc:
            continue
        client.put_agent(name, doc, f"{run_id}:prepare:{name}")


def _close_outstanding(client: FleetClient, items: list[dict[str, Any]], run_id: str) -> list[dict[str, Any]]:
    receipts = []
    for item in items:
        projection = require_execution_projection(item)
        if projection["is_terminal"]:
            continue
        try:
            response = client.close_task(item["id"], f"{run_id}:budget-close:{item['id']}", "platform-eval-budget")
            receipts.append({"task_id": item["id"], "status": "closed", "response": response})
        except Exception as exc:
            receipts.append({"task_id": item["id"], "status": "close_failed", "error": str(exc)[:500]})
    return receipts


def execute_core_run(
    client: FleetClient,
    inputs: dict[str, Any],
    output_dir: Path,
    mode: str,
    *,
    active_human_seconds: float = 0,
    independent_evidence: dict[str, Any] | None = None,
    prepare: bool = True,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Execute or resume one real run; no report is persisted by this function."""
    if mode not in {"single", "multi"}:
        raise ValueError("mode 必须为 single 或 multi")
    scenario = inputs["scenario"]
    runner = scenario["runner"]
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = output_dir.name
    prompt = build_prompt(inputs, mode)
    context = {
        "scenario_id": scenario["id"],
        "north_star_contract": SZ_VSR,
        "mode": mode,
        "prompt_version": runner["prompt_version"],
        "model_profile": runner["model_profile"],
        "fixtures_sha256": inputs["fixtures_sha256"],
        "prompt_sha256": sha256_json(prompt),
    }
    state_path = output_dir / "run-state.json"
    if state_path.is_file():
        state = read_json(state_path)
        if state.get("context") != context:
            raise ExecutionError("续跑上下文与已保存状态不一致；请使用新的输出目录")
    else:
        state = {"context": context, "run_id": run_id, "started_at": utc_now(), "history": []}
        if prepare:
            _prepare_agents(client, run_id)
        agent = runner["single_agent"] if mode == "single" else runner["orchestrator"]
        state["root_task_id"] = client.dispatch(
            agent, prompt, runner["model_profile"], f"{run_id}:{mode}:root", "platform-eval")
        state["root_agent"] = agent
        atomic_write_json(state_path, state)

    ledger = HumanTimeLedger()
    if active_human_seconds > 0:
        ledger.add("maintainer_trigger", active_human_seconds, "外部实测的主动人工时间")
    for event in (independent_evidence or {}).get("human_events") or []:
        ledger.add(event["kind"], float(event["seconds"]), event.get("note") or "")

    limits = scenario["stop_conditions"]
    started_epoch = datetime.fromisoformat(state["started_at"]).timestamp()
    elapsed_before_resume = max(0.0, time.time() - started_epoch)
    budget = ExecutionBudget(
        max_wall_seconds=float(limits["max_minutes"]) * 60,
        max_tokens=int(limits["max_tokens"]),
        started_monotonic=time.monotonic() - elapsed_before_resume,
    )
    root_id = int(state["root_task_id"])
    detail: dict[str, Any] = {}
    items: list[dict[str, Any]] = []
    early_delivery = bool(state.get("early_delivery"))
    terminal_reason = "pending"
    close_receipts: list[dict[str, Any]] = []
    last_signature = None

    while True:
        detail = client.task_detail(root_id)
        child_ids = [int(child["id"]) for child in detail.get("children") or []]
        items = client.tasks([root_id, *child_ids])
        by_id = {int(item["id"]): item for item in items}
        root = by_id[root_id]
        root_projection = require_execution_projection(root)
        children = [by_id[child_id] for child_id in child_ids]
        children_terminal = all(require_execution_projection(child)["is_terminal"] for child in children)
        max_version = max((int(loop.get("version") or 1) for loop in detail.get("loops") or []), default=1)
        if mode == "multi" and root_projection["execution_status"] == "succeeded" and not children_terminal:
            early_delivery = True
            state["early_delivery"] = True
        threads = _thread_ids(items, detail)
        if threads:
            usage = _collect_tokens(client, threads)
            budget.set_total_tokens(usage["total_tokens"])
            state["token_usage"] = usage
        signature = _task_status_signature(items)
        if signature != last_signature:
            state["history"].append({"at": utc_now(), "tasks": signature, "max_root_loop_version": max_version})
            last_signature = signature
        state["token_usage"] = state.get("token_usage") or token_totals([])
        state["budget"] = budget.snapshot()
        atomic_write_json(state_path, state)

        try:
            budget.check()
        except BudgetExceeded as exc:
            terminal_reason = str(exc)
            close_receipts = _close_outstanding(client, items, run_id)
            break

        if root_projection["execution_status"] in {"failed", "closed"}:
            terminal_reason = root_projection["execution_status"]
            close_receipts = _close_outstanding(client, children, run_id)
            break
        if root_projection["execution_status"] == "succeeded" and children_terminal:
            if mode == "single" or not early_delivery or max_version >= 2:
                terminal_reason = "succeeded"
                break
        sleep(float(runner.get("poll_seconds", 5)))

    delivery = delivery_from_detail(detail)
    output = delivery.get("deliverable")
    checks = validate_delivery(output, inputs["expected"], {
        "fleet_status": inputs["fleet"], "timeline_boards": inputs["timeline"],
    })
    children = [item for item in items if int(item["id"]) != root_id]
    max_version = max((int(loop.get("version") or 1) for loop in detail.get("loops") or []), default=1)
    if mode == "single":
        mode_checks = {"no_child_tasks": len(children) == 0}
    else:
        expected_workers = set(runner["workers"].values())
        system_resume = any(
            comment.get("author") == "system:child-completion"
            for comment in detail.get("comments") or []
        )
        mode_checks = {
            "at_least_two_children": len(children) >= 2,
            "expected_distinct_workers": {child.get("agent_name") for child in children} == expected_workers,
            # 真实唤醒事件 + 再次交付是硬证据；轮询是否看到提前交付只是诊断信息。
            "auto_resume_version_gte_2": system_resume and max_version >= 2,
            "no_human_resume": ledger.count("human_resume") == 0,
        }
    all_checks = {**checks, **mode_checks}
    accepted_goal = int(terminal_reason == "succeeded" and all(all_checks.values()))
    vgh = vgh_measurement(accepted_goal, ledger.total_seconds)
    usage = state.get("token_usage") or token_totals([])
    result = {
        **context,
        "run_id": run_id,
        "root_task_id": root_id,
        "root_agent": state["root_agent"],
        "started_at": state["started_at"],
        "finished_at": utc_now(),
        "status": terminal_reason,
        "accepted_goal": accepted_goal,
        "checks": all_checks,
        "delivery": output,
        "parsed_delivery": parse_json_object(output) if checks["json_object"] else None,
        "task_ids": [root_id, *(int(child["id"]) for child in children)],
        "child_tasks": [
            {
                "task_id": child["id"],
                "agent_name": child.get("agent_name"),
                "latest_loop_id": child.get("latest_loop_id"),
                "latest_thread_id": child.get("latest_thread_id"),
                **require_execution_projection(child),
            }
            for child in children
        ],
        "max_root_loop_version": max_version,
        "early_delivery": early_delivery,
        "total_tokens": usage["total_tokens"],
        "token_usage": usage,
        "wall_seconds": budget.elapsed_seconds,
        "budget": budget.snapshot(),
        "human": ledger.snapshot(),
        **vgh,
        "close_receipts": close_receipts,
        "history": state["history"],
        "production_write_count": 0,
        "auto_development_allowed": False,
    }
    result["zero_human_verified_success"] = zero_human_verified_success(result)
    atomic_write_json(output_dir / "evidence.json", {
        "root_detail": detail,
        "task_summaries": items,
        "scenario_sha256": sha256_json(scenario),
        "fixtures_sha256": inputs["fixtures_sha256"],
    })
    atomic_write_json(output_dir / "run.json", result)
    return result


def validate_baseline(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    required = (
        "scenario_id", "prompt_version", "model_profile", "fixtures_sha256", "accepted_goal",
    )
    missing = [key for key in required if key not in baseline]
    if missing:
        raise ExecutionError(f"对照基线缺字段：{missing}")
    if baseline.get("north_star_contract") != SZ_VSR or candidate.get("north_star_contract") != SZ_VSR:
        raise ExecutionError("新旧指标协议不可混用；请使用 SZ-VSR 口径重新建立基线")
    baseline_success = zero_human_verified_success(baseline)
    candidate_success = zero_human_verified_success(candidate)
    if baseline.get("mode") != "single" or baseline_success != 1:
        raise ExecutionError("对照基线必须是零人工且独立验收通过的 single 运行")
    if candidate_success is None:
        raise ExecutionError("候选缺少零人工验收判定所需证据")
    for key in ("scenario_id", "prompt_version", "model_profile", "fixtures_sha256"):
        if baseline.get(key) != candidate.get(key):
            raise ExecutionError(f"对照基线与候选的 {key} 不一致")
    vgh_delta = None
    if baseline.get("vgh") is not None and candidate.get("vgh") is not None:
        vgh_delta = candidate["vgh"] - baseline["vgh"]
    return {
        "comparable": True,
        "baseline_run_id": baseline["run_id"],
        "baseline_sz_vsr": baseline_success,
        "candidate_sz_vsr": candidate_success,
        "sz_vsr_delta": candidate_success - baseline_success,
        "baseline_vgh": baseline.get("vgh"),
        "candidate_vgh": candidate.get("vgh"),
        "vgh_delta": vgh_delta,
        "baseline_tokens": baseline.get("total_tokens"),
        "candidate_tokens": candidate.get("total_tokens"),
        "baseline_wall_seconds": baseline.get("wall_seconds"),
        "candidate_wall_seconds": candidate.get("wall_seconds"),
    }


def build_measurement(
    result: dict[str, Any],
    scenario: dict[str, Any],
    platform_version: str,
    independent: dict[str, Any] | None = None,
    baseline_comparison: dict[str, Any] | None = None,
) -> dict[str, Any]:
    independent = independent or {}
    candidate = result["mode"] == "multi"
    accepted = result["accepted_goal"] == 1
    baseline = baseline_comparison or {"comparable": False, "reason": "no_valid_single_agent_control"}
    comparable = bool(baseline.get("comparable"))
    if not candidate:
        gates = {"P0": False, "P1": False, "P2": False}
    else:
        gates = {
            "P0": accepted,
            "P1": independent.get("P1") is True,
            "P2": independent.get("P2") is True,
        }
    playground_level = independent.get("playground_level", 0) if candidate else 0
    if not isinstance(playground_level, int) or isinstance(playground_level, bool) or not 0 <= playground_level <= 5:
        raise ExecutionError("playground_level 必须是 0–5 整数")
    engineering_level = 0
    if candidate and comparable:
        engineering_level = 3 if (
            result["vgh"] is not None
            and baseline.get("baseline_vgh") is not None
            and result["vgh"] >= baseline["baseline_vgh"]
        ) else 2
    return {
        "platform_version": platform_version,
        "scenario_version": result["scenario_id"],
        "gates": gates,
        "dimensions": {
            "task_goal": 4 if accepted else 1,
            "multi_agent": 4 if candidate and accepted else 0,
            "protocol": 4 if result["status"] == "succeeded" else 1,
            "runtime": 3 if result["token_usage"].get("total_runs", 0) > 0 else 1,
            "evidence_auth": 4 if result["checks"].get("source_operations") and result["checks"].get("source_captured_at") else 0,
            "playground": playground_level,
            "engineering_cost": engineering_level,
        },
        "baseline": baseline,
        "north_star": {
            "candidate_sz_vsr": zero_human_verified_success(result),
            "baseline_sz_vsr": baseline.get("baseline_sz_vsr") if comparable else None,
            "candidate_vgh": result["vgh"],
            "baseline_vgh": baseline.get("baseline_vgh") if comparable else None,
            "human_intervention_count": result["human"].get("human_intervention_count"),
            "guardrails": independent.get("guardrails"),
        },
        "repeatability": independent.get("repeatability") or {
            "runtime_types": 1,
            "third_party_agents": 0,
            "consecutive_stable_runs": 0,
        },
        "progress": {
            "no_improvement_rounds": int(independent.get("no_improvement_rounds") or 0),
            "repeated_failure": bool(independent.get("repeated_failure")),
            "experiments": int(independent.get("experiments") or 1),
            "tokens": result["total_tokens"],
            "minutes": result["wall_seconds"] / 60,
            "no_new_hypothesis": bool(independent.get("no_new_hypothesis")),
            "maintainer_stop": bool(independent.get("maintainer_stop")),
        },
        "evidence_refs": {
            "run_id": result["run_id"],
            "root_task_id": result["root_task_id"],
            "task_ids": result["task_ids"],
            "accepted_goal": result["accepted_goal"],
            "zero_human_verified_success": zero_human_verified_success(result),
            "active_human_seconds": result["active_human_seconds"],
            "vgh": result["vgh"],
            "vgh_measurement_status": result["measurement_status"],
        },
    }


def deterministic_recommendations(scorecard: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    dimensions = scorecard["dimension_scores"]
    weakest = sorted(
        dimensions,
        key=lambda key: (dimensions[key]["score"] / DIMENSIONS[key], DIMENSIONS[key]),
    )
    return [
        {
            "dimension": key,
            "summary": RECOMMENDATION_TEXT[key],
            "evidence": {"run_id": run_id, "dimension_score": dimensions[key]},
        }
        for key in weakest[:2]
    ]


def validate_recommendations(value: Any) -> list[dict[str, Any]]:
    parsed = parse_json_object(value)
    raw = parsed.get("recommendations")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 3:
        raise ExecutionError("fleet-eval-01 必须返回 1–3 条 recommendations")
    result = []
    for item in raw:
        if not isinstance(item, dict) or item.get("dimension") not in DIMENSIONS:
            raise ExecutionError("建议包含未知 dimension")
        summary = item.get("summary")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 2000:
            raise ExecutionError("建议 summary 无效")
        result.append({
            "dimension": item["dimension"],
            "summary": summary.strip(),
            "evidence": item.get("evidence") if isinstance(item.get("evidence"), dict) else {},
        })
    return result


def interpret_with_eval_agent(
    client: FleetClient,
    result: dict[str, Any],
    scorecard: dict[str, Any],
    scenario: dict[str, Any],
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    prompt = f"""你是 fleet-eval-01，只解释真实 core-v1 结果，不修改题目、阈值或分数。
以下是确定性评分卡：{json.dumps(scorecard, ensure_ascii=False)}
以下是运行证据摘要：{json.dumps({key: result[key] for key in ('run_id', 'mode', 'status', 'checks', 'total_tokens', 'wall_seconds', 'vgh')}, ensure_ascii=False)}
只输出 JSON：{{"recommendations":[{{"dimension":"七维之一","summary":"一个有边界的下一轮实验","evidence":{{}}}}]}}，最多 3 条。
建议不得自动变成开发 Task，不得建议自动合入代码。"""
    task_id = client.dispatch(
        EVAL_AGENT, prompt, scenario["runner"]["model_profile"],
        f"{result['run_id']}:interpret", "platform-eval")
    deadline = time.monotonic() + min(300, float(scenario["stop_conditions"]["max_minutes"]) * 60)
    while True:
        detail = client.task_detail(task_id)
        delivery = delivery_from_detail(detail)
        if delivery["state"] == "succeeded":
            recommendations = validate_recommendations(delivery["deliverable"])
            loop = latest_loop(detail) or {}
            usage = client.token_usage(loop["thread_id"]) if loop.get("thread_id") else token_totals([])
            return recommendations, {"task_id": task_id, "status": "completed", "token_usage": usage}
        if delivery["state"] in {"failed", "closed"}:
            raise ExecutionError(f"fleet-eval-01 解释失败：{delivery['state']}")
        if time.monotonic() >= deadline:
            try:
                client.close_task(task_id, f"{result['run_id']}:interpret:timeout-close", "platform-eval-timeout")
            except Exception as exc:
                raise ExecutionError(f"fleet-eval-01 解释超时且取消失败：{exc}") from exc
            raise ExecutionError("fleet-eval-01 解释超时；已请求取消活动 Run")
        sleep(float(scenario["runner"].get("poll_seconds", 5)))


def run_core_evaluation(
    client: FleetClient,
    output_dir: Path,
    mode: str,
    *,
    scenario_path: Path = DEFAULT_SCENARIO,
    active_human_seconds: float = 0,
    independent_evidence: dict[str, Any] | None = None,
    baseline: dict[str, Any] | None = None,
    persist: bool = False,
    interpret: bool = False,
    prepare: bool = True,
    platform_version: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if persist and mode != "multi":
        raise ExecutionError("single 对照不得持久化为平台候选报告；拒绝在创建任务前持久化")
    inputs = load_core_inputs(scenario_path)
    scenario = inputs["scenario"]
    result = execute_core_run(
        client, inputs, output_dir, mode,
        active_human_seconds=active_human_seconds,
        independent_evidence=independent_evidence,
        prepare=prepare,
        sleep=sleep,
    )
    comparison = validate_baseline(baseline, result) if baseline is not None else None
    platform_version = platform_version or current_platform_version()
    measurement = build_measurement(result, scenario, platform_version, independent_evidence, comparison)
    scorecard = calculate_scorecard(measurement, scenario)
    recommendations = deterministic_recommendations(scorecard, result["run_id"]) if mode == "multi" else []
    interpretation: dict[str, Any] = {"status": "not_run"}
    interpret_started = time.monotonic()
    if interpret and mode == "multi":
        try:
            recommendations, interpretation = interpret_with_eval_agent(
                client, result, scorecard, scenario, sleep=sleep)
            extra = int(interpretation["token_usage"].get("total_tokens") or 0)
            result["total_tokens"] += extra
            result["token_usage"] = token_totals([result["token_usage"], interpretation["token_usage"]])
            if result["total_tokens"] >= int(scenario["stop_conditions"]["max_tokens"]):
                result["status"] = "token_budget_exhausted"
                result["accepted_goal"] = 0
                result.update(vgh_measurement(0, result["active_human_seconds"]))
        except Exception as exc:
            interpretation = {"status": "failed", "error": str(exc)[:1000]}
            result["status"] = "interpretation_failed"
            result["accepted_goal"] = 0
            result.update(vgh_measurement(0, result["active_human_seconds"]))
            recommendations = deterministic_recommendations(scorecard, result["run_id"])
        result["wall_seconds"] += time.monotonic() - interpret_started
        result["budget"]["elapsed_seconds"] = result["wall_seconds"]
        result["budget"]["total_tokens"] = result["total_tokens"]
        if result["wall_seconds"] >= float(scenario["stop_conditions"]["max_minutes"]) * 60 and result["status"] == "succeeded":
            result["status"] = "time_budget_exhausted"
            result["accepted_goal"] = 0
            result.update(vgh_measurement(0, result["active_human_seconds"]))
        if result["status"] in {"time_budget_exhausted", "token_budget_exhausted"}:
            result["budget"]["stop_reason"] = result["status"]
        if baseline is not None:
            comparison = validate_baseline(baseline, result)
        measurement = build_measurement(result, scenario, platform_version, independent_evidence, comparison)
        scorecard = calculate_scorecard(measurement, scenario)
    result["measurement"] = measurement
    result["scorecard"] = scorecard
    result["baseline_comparison"] = comparison
    result["interpretation"] = interpretation
    result["recommendations"] = recommendations
    report = None
    if persist:
        report = client.create_report({
            "scenario_id": scenario["id"],
            "measurement": measurement,
            "recommendations": recommendations,
        }, f"{result['run_id']}:report")
    result["report"] = report
    atomic_write_json(output_dir / "measurement.json", measurement)
    atomic_write_json(output_dir / "scorecard.json", scorecard)
    atomic_write_json(output_dir / "run.json", result)
    return result


def validate_offline(scenario_path: Path) -> dict[str, Any]:
    inputs = load_core_inputs(scenario_path)
    for mode in ("single", "multi"):
        prompt = build_prompt(inputs, mode)
        if "expected/facts.json" in prompt:
            raise ExecutionError(f"{mode} 输入不得引用预期答案文件")
    agents = {}
    for name, filename in AGENT_FILES.items():
        doc = yaml.safe_load((ROOT / "control-plane/agents" / filename).read_text(encoding="utf-8"))
        agent = doc["spec"]["agent"]
        if agent.get("memory_enabled") is not False or agent.get("allowed_subagents") != []:
            raise ExecutionError(f"{name} 未满足零记忆／零子 Agent")
        if agent.get("tool_groups") != [] or doc.get("egress") != []:
            raise ExecutionError(f"{name} 未满足零工具／零出站")
        agents[name] = sha256_json(doc)
    return {
        "status": "passed",
        "scenario_id": inputs["scenario"]["id"],
        "modes": ["single", "multi"],
        "fixtures_sha256": inputs["fixtures_sha256"],
        "agents_sha256": agents,
        "real_tasks_started": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    run = sub.add_parser("run")
    run.add_argument("--mode", choices=("single", "multi"), required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    run.add_argument("--api", default=os.environ.get("FLEET_API", "http://127.0.0.1:8003"))
    run.add_argument("--active-human-seconds", type=float, default=0)
    run.add_argument("--baseline", type=Path)
    run.add_argument("--independent-evidence", type=Path)
    run.add_argument("--persist", action="store_true")
    run.add_argument("--interpret", action="store_true")
    run.add_argument("--no-prepare", action="store_true")
    args = parser.parse_args()
    if args.command == "validate":
        print(json.dumps(validate_offline(args.scenario), ensure_ascii=False, indent=2))
        return 0
    output = artifact_path(ARTIFACT_ROOT, args.output)
    baseline = read_json(artifact_path(ARTIFACT_ROOT, args.baseline)) if args.baseline else None
    independent = read_json(artifact_path(ARTIFACT_ROOT, args.independent_evidence)) if args.independent_evidence else None
    client = FleetClient(args.api, os.environ.get("FLEET_PAT") or None)
    result = run_core_evaluation(
        client, output, args.mode,
        scenario_path=args.scenario,
        active_human_seconds=args.active_human_seconds,
        independent_evidence=independent,
        baseline=baseline,
        persist=args.persist,
        interpret=args.interpret,
        prepare=not args.no_prepare,
    )
    print(json.dumps({
        "run_id": result["run_id"], "status": result["status"],
        "accepted_goal": result["accepted_goal"], "vgh": result["vgh"],
        "score": result["scorecard"]["score"], "rating": result["scorecard"]["rating"],
        "report_id": (result.get("report") or {}).get("report_id"),
        "output": str(output), "auto_development_allowed": False,
    }, ensure_ascii=False, indent=2))
    return 0 if result["accepted_goal"] == 1 else 2


if __name__ == "__main__":
    raise SystemExit(main())
