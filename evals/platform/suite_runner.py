#!/usr/bin/env python3
"""Extensible platform case runner and SZ-VSR suite summary (ADR-0045).

``validate`` and ``summarize`` are offline. Real work starts only through an
explicit ``run`` command. Runtime HTTP providers are controlled test services,
never business-website adapters.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import yaml

from evals.platform.core_runner import run_core_evaluation, validate_offline
from evals.platform.execution import (
    BudgetExceeded,
    ExecutionBudget,
    ExecutionError,
    FleetClient,
    HumanTimeLedger,
    artifact_path,
    atomic_write_json,
    delivery_from_detail,
    parse_json_object,
    read_json,
    require_execution_projection,
    sha256_json,
    token_totals,
    utc_now,
    vgh_measurement,
)
from evals.platform.north_star import SZ_VSR, load_suite, summarize_suite, zero_human_verified_success
from evals.platform.scorecard import load_scenario

ROOT = Path(__file__).resolve().parents[2]
PLATFORM_ROOT = ROOT / "evals/platform"
DEFAULT_SUITE = PLATFORM_ROOT / "suite.yaml"
ARTIFACT_ROOT = ROOT / "artifacts/platform-evals"
AGENT_FILES = {
    "fleet-eval-general-01": "fleet-eval-general-01.yaml",
    "fleet-eval-source-a-01": "fleet-eval-source-a-01.yaml",
    "fleet-eval-source-b-01": "fleet-eval-source-b-01.yaml",
}


def load_case(suite_path: Path, case_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    suite = load_suite(suite_path)
    definition = next((case for case in suite["cases"] if case["id"] == case_id), None)
    if definition is None:
        raise ExecutionError(f"未知评测 case：{case_id}")
    scenario_path = artifact_path(PLATFORM_ROOT, definition["scenario"])
    scenario = load_scenario(scenario_path)
    if scenario["id"] != case_id:
        raise ExecutionError("集合 case id 与 scenario id 不一致")
    return suite, definition, scenario


def _new_case_data(case_type: str) -> dict[str, Any]:
    if case_type == "multi_echo":
        return {
            "part_a": f"ECHO-A-{uuid.uuid4().hex[:10].upper()}",
            "part_b": f"ECHO-B-{uuid.uuid4().hex[:10].upper()}",
        }
    if case_type == "external_info":
        return {
            "observation": {
                "observation_id": f"OBS-{uuid.uuid4().hex[:10].upper()}",
                "value": f"RUNTIME-{uuid.uuid4().hex[:12].upper()}",
                "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        }
    if case_type == "time_now":
        return {}
    raise ExecutionError(f"不支持的 case_type：{case_type}")


def build_prompt(scenario: dict[str, Any], mode: str, case_data: dict[str, Any], run_id: str) -> str:
    runner = scenario["runner"]
    case_type = scenario["case_type"]
    version = runner["prompt_version"]
    if case_type == "time_now":
        return f"""【time-now-v1 平台时间证据｜{version}】
使用 threads.state 读取当前 Fleet Thread 的服务端 Checkpoint 时间，禁止凭模型内部时间作答，禁止派生子任务或请求人工继续。
最终只输出一个 JSON 对象：
{{
  "captured_at": "<直接来自 Checkpoint 的 ISO 时间>",
  "local_time": "<换算后的 Asia/Shanghai ISO 时间，含 +08:00>",
  "source": {{"type": "fleet_checkpoint", "thread_id": "<当前 Thread ID>"}}
}}
不要输出 Markdown 代码围栏，不要编造其他字段。"""
    if case_type == "multi_echo":
        if mode == "single":
            return f"""【multi-echo-v1 单 Agent 对照｜{version}】
独立完成以下确定性组装，禁止调用工具、派生子任务或请求人工继续。
part_a={case_data['part_a']}
part_b={case_data['part_b']}
最终只输出 JSON：{{"parts":[part_a,part_b],"joined":"part_a|part_b"}}，将上述两个值原样填入。"""
        return f"""【multi-echo-v1 多 Agent 自动唤醒｜{version}】
必须真实验证协作链，不得由自己直接生成最终交付：
1. 并行创建两个子 Task，分别指派 {runner['workers']['part_a']} 和 {runner['workers']['part_b']}。第一个子任务只要求原样返回 {case_data['part_a']}，第二个只要求原样返回 {case_data['part_b']}。
2. 创建后先交付简短阶段说明并结束当前回合，不得请求人工继续。
3. 收到 system:child-completion 自动唤醒后，读取两个真实子任务 OUTPUT，最终只输出 JSON：{{"parts":["{case_data['part_a']}","{case_data['part_b']}"],"joined":"{case_data['part_a']}|{case_data['part_b']}"}}。
4. 不得重复派发、验收或关闭任务，不得输出 Markdown 代码围栏。"""
    if case_type == "external_info":
        artifact = f"ExternalObservation.{run_id}"
        operation = scenario["runner"]["operation_id"]
        return f"""【external-info-v1 运行时外部信息获取｜{version}】
目标：获取一条当前运行时新注册的外部观测，禁止派生子任务、读取评测文件或请求人工继续。
1. 使用 capabilities.list／resolve 按 output_artifact={artifact} 和 operation_id={operation} 找到已验证的只读能力；不要假设固定 provider_id。
2. 使用 capabilities.invoke 真实调用该能力，input 为 {{}}。不得直接编造观测值，不得修改 Provider 或任何业务数据。
3. 最终只输出一个 JSON 对象：
{{
  "observation": {{"observation_id": "<调用返回值>", "value": "<调用返回值>", "captured_at": "<调用返回值>"}},
  "source": {{"capability_id": "<实际调用 ID>", "invocation_id": <实际 invocation 整数>}}
}}
不要输出 Markdown 代码围栏，不要添加调用结果之外的观测事实。"""
    raise ExecutionError(f"不支持的 case_type：{case_type}")


class _ObservationHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _reply(self, value: dict[str, Any], status: int = 200) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/health":
            return self._reply({"status": "ok"})
        return self._reply({"detail": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        if self.path == "/api/observation":
            return self._reply(self.server.observation)
        return self._reply({"detail": "not found"}, 404)


class _ExternalService:
    def __init__(self, observation: dict[str, Any]):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _ObservationHandler)
        self.server.observation = observation
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"


def _prepare_agents(client: FleetClient, run_id: str, case_type: str) -> None:
    names = ["fleet-eval-general-01"]
    if case_type == "multi_echo":
        names += ["fleet-eval-source-a-01", "fleet-eval-source-b-01"]
    for name in names:
        doc = yaml.safe_load((ROOT / "control-plane/agents" / AGENT_FILES[name]).read_text(encoding="utf-8"))
        current = client.get_agent(name)
        if current and current.get("yaml") == doc:
            continue
        client.put_agent(name, doc, f"{run_id}:prepare:{name}")


def _provider_manifest(scenario: dict[str, Any], run_id: str, base_url: str) -> dict[str, Any]:
    safe_run_id = re.sub(r"[^a-z0-9-]", "-", run_id.lower())[:80]
    provider_id = f"eval-external-{safe_run_id}"
    operation = scenario["runner"]["operation_id"]
    return {
        "provider_id": provider_id,
        "provider_type": "remote_agent",
        "capabilities": [{
            "operation_id": operation,
            "input_schema": {"type": "object"},
            "output_schema": {
                "type": "object",
                "required": ["observation_id", "value", "captured_at"],
                "properties": {
                    "observation_id": {"type": "string"},
                    "value": {"type": "string"},
                    "captured_at": {"type": "string"},
                },
            },
            "output_artifact": f"ExternalObservation.{run_id}",
            "risk": "read",
            "version": 1,
            "request": {"method": "POST", "path": "/api/observation"},
        }],
        "config": {
            "protocol": "http",
            "base_url": base_url,
            "allow_private": True,
            "timeout_seconds": 10,
        },
    }


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _state_times(value: Any) -> list[datetime]:
    found = []
    if isinstance(value, dict):
        for item in value.values():
            found += _state_times(item)
    elif isinstance(value, list):
        for item in value:
            found += _state_times(item)
    else:
        parsed = _parse_time(value)
        if parsed is not None:
            found.append(parsed)
    return found


def _validate_time(
    output: Any,
    detail: dict[str, Any],
    state: dict[str, Any],
    client: FleetClient,
) -> tuple[dict[str, bool], dict[str, Any]]:
    try:
        delivery = parse_json_object(output)
    except ExecutionError:
        return {key: False for key in (
            "json_object", "checkpoint_timestamp", "checkpoint_source", "time_window", "timezone_conversion",
        )}, {}
    loop = max(detail.get("loops") or [], key=lambda item: int(item.get("id") or 0), default={})
    thread_id = loop.get("thread_id")
    evidence = {"thread_id": thread_id, "checkpoint_timestamps": []}
    try:
        thread_state = client.thread_state(thread_id) if thread_id else {}
    except ExecutionError as exc:
        thread_state = {}
        evidence["thread_state_error"] = str(exc)[:500]
    timestamps = _state_times(thread_state)
    evidence["checkpoint_timestamps"] = sorted({item.isoformat() for item in timestamps})
    captured = _parse_time(delivery.get("captured_at"))
    local = _parse_time(delivery.get("local_time"))
    source = delivery.get("source") or {}
    started = _parse_time(state["started_at"])
    now = datetime.now(timezone.utc)
    time_window = bool(
        captured and started
        and abs((captured - started).total_seconds()) <= 300
        and abs((now - captured).total_seconds()) <= 300
    )
    expected_local = captured.astimezone(ZoneInfo("Asia/Shanghai")) if captured else None
    nearest_checkpoint_seconds = (
        min(abs((captured - item).total_seconds()) for item in timestamps)
        if captured and timestamps else None
    )
    evidence["nearest_checkpoint_seconds"] = nearest_checkpoint_seconds
    return {
        "json_object": True,
        # 交付会生成新的 Checkpoint；允许读取时刻与后续 Checkpoint 的秒级推进。
        "checkpoint_timestamp": nearest_checkpoint_seconds is not None and nearest_checkpoint_seconds <= 5,
        "checkpoint_source": source.get("type") == "fleet_checkpoint" and source.get("thread_id") == thread_id,
        "time_window": time_window,
        "timezone_conversion": bool(local and expected_local and local == expected_local),
    }, evidence


def _validate_external(
    output: Any,
    case_data: dict[str, Any],
    provider: dict[str, Any],
    client: FleetClient,
) -> tuple[dict[str, bool], dict[str, Any]]:
    try:
        delivery = parse_json_object(output)
    except ExecutionError:
        delivery = {}
    evidence = {}
    try:
        card = client.provider_card(provider["provider_id"])
        evidence["provider_card"] = card
    except ExecutionError as exc:
        card = {}
        evidence["provider_card_error"] = str(exc)[:500]
    performance = card.get("performance") or {}
    observation = delivery.get("observation") or {}
    source = delivery.get("source") or {}
    expected = case_data["observation"]
    capability_id = f"{provider['provider_id']}/{provider['capabilities'][0]['operation_id']}"
    invocation_id = source.get("invocation_id")
    checks = {
        "json_object": bool(delivery),
        "runtime_observation": all(observation.get(key) == expected[key] for key in expected),
        "runtime_capability_source": source.get("capability_id") == capability_id,
        "invocation_id": isinstance(invocation_id, int) and not isinstance(invocation_id, bool) and invocation_id > 0,
        "real_invocation": int(performance.get("invocations") or 0) >= 1 and float(performance.get("success_rate") or 0) > 0,
    }
    return checks, evidence


def _validate_echo(output: Any, case_data: dict[str, Any]) -> dict[str, bool]:
    try:
        delivery = parse_json_object(output)
    except ExecutionError:
        return {"json_object": False, "parts": False, "joined": False}
    expected = [case_data["part_a"], case_data["part_b"]]
    return {
        "json_object": True,
        "parts": delivery.get("parts") == expected,
        "joined": delivery.get("joined") == "|".join(expected),
    }


def _close_outstanding(
    client: FleetClient,
    items: list[dict[str, Any]],
    run_id: str,
) -> list[dict[str, Any]]:
    receipts = []
    for item in items:
        if require_execution_projection(item)["is_terminal"]:
            continue
        try:
            response = client.close_task(item["id"], f"{run_id}:budget-close:{item['id']}", "platform-eval-budget")
            receipts.append({"task_id": item["id"], "status": "closed", "response": response})
        except Exception as exc:
            receipts.append({"task_id": item["id"], "status": "close_failed", "error": str(exc)[:500]})
    return receipts


def execute_case_run(
    client: FleetClient,
    scenario: dict[str, Any],
    output_dir: Path,
    mode: str,
    *,
    active_human_seconds: float = 0,
    iteration_id: str | None = None,
    prepare: bool = True,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if mode not in {"single", "multi"}:
        raise ValueError("mode 必须为 single 或 multi")
    case_type = scenario["case_type"]
    if case_type not in {"time_now", "multi_echo", "external_info"}:
        raise ExecutionError(f"通用启动器不支持 {case_type}")
    runner = scenario["runner"]
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = output_dir.name
    state_path = output_dir / "run-state.json"
    if state_path.is_file():
        state = read_json(state_path)
        case_data = state["case_data"]
    else:
        case_data = _new_case_data(case_type)
        state = {}
    prompt = build_prompt(scenario, mode, case_data, run_id)
    context = {
        "scenario_id": scenario["id"],
        "north_star_contract": SZ_VSR,
        "iteration_id": iteration_id,
        "case_type": case_type,
        "mode": mode,
        "prompt_version": runner["prompt_version"],
        "model_profile": runner["model_profile"],
        "case_data_sha256": sha256_json(case_data),
        "prompt_sha256": sha256_json(prompt),
    }
    if state and state.get("context") != context:
        raise ExecutionError("续跑上下文与已保存状态不一致；请使用新的输出目录")

    external = _ExternalService(case_data["observation"]) if case_type == "external_info" else None
    if external is not None:
        external.__enter__()
    try:
        provider = None
        if not state:
            state = {
                "context": context,
                "run_id": run_id,
                "case_data": case_data,
                "started_at": utc_now(),
                "history": [],
            }
            if prepare:
                _prepare_agents(client, run_id, case_type)
            if external is not None:
                provider = _provider_manifest(scenario, run_id, external.base_url)
                client.register_provider(provider, f"{run_id}:provider")
                client.verify_provider(provider["provider_id"], f"{run_id}:provider:verify")
            agent = runner["single_agent"] if mode == "single" else runner["orchestrator"]
            state["root_task_id"] = client.dispatch(
                agent, prompt, runner["model_profile"], f"{run_id}:{mode}:root", "platform-eval")
            state["root_agent"] = agent
            atomic_write_json(state_path, state)
        elif external is not None:
            provider = _provider_manifest(scenario, run_id, external.base_url)
            client.register_provider(provider, f"{run_id}:provider:resume")
            client.verify_provider(provider["provider_id"], f"{run_id}:provider:resume:verify")
        if provider is None and case_type == "external_info":
            provider = _provider_manifest(scenario, run_id, external.base_url)

        ledger = HumanTimeLedger()
        if active_human_seconds > 0:
            ledger.add("maintainer_trigger", active_human_seconds, "外部实测的主动人工时间")
        limits = scenario["stop_conditions"]
        started_epoch = datetime.fromisoformat(state["started_at"]).timestamp()
        budget = ExecutionBudget(
            max_wall_seconds=float(limits["max_minutes"]) * 60,
            max_tokens=int(limits["max_tokens"]),
            started_monotonic=time.monotonic() - max(0.0, time.time() - started_epoch),
        )
        root_id = int(state["root_task_id"])
        detail = {}
        items = []
        early_delivery = bool(state.get("early_delivery"))
        terminal_reason = "pending"
        close_receipts = []
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
            if case_type == "multi_echo" and mode == "multi" and root_projection["execution_status"] == "succeeded" and not children_terminal:
                early_delivery = True
                state["early_delivery"] = True
            thread_ids = {
                item["latest_thread_id"] for item in items if item.get("latest_thread_id")
            }
            thread_ids.update(
                loop["thread_id"] for loop in detail.get("loops") or [] if loop.get("thread_id")
            )
            if thread_ids:
                usage = token_totals([client.token_usage(thread_id) for thread_id in sorted(thread_ids)])
                budget.set_total_tokens(usage["total_tokens"])
                state["token_usage"] = usage
            signature = [
                {"task_id": item["id"], **require_execution_projection(item)}
                for item in sorted(items, key=lambda entry: entry["id"])
            ]
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
                if case_type != "multi_echo" or mode == "single" or not early_delivery or max_version >= 2:
                    terminal_reason = "succeeded"
                    break
            sleep(float(runner.get("poll_seconds", 3)))

        output = delivery_from_detail(detail).get("deliverable")
        extra_evidence = {}
        if case_type == "time_now":
            checks, extra_evidence = _validate_time(output, detail, state, client)
        elif case_type == "external_info":
            checks, extra_evidence = _validate_external(output, case_data, provider, client)
        else:
            checks = _validate_echo(output, case_data)
        children = [item for item in items if int(item["id"]) != root_id]
        max_version = max((int(loop.get("version") or 1) for loop in detail.get("loops") or []), default=1)
        if case_type == "multi_echo" and mode == "multi":
            expected_workers = set(runner["workers"].values())
            system_resume = any(
                comment.get("author") == "system:child-completion"
                for comment in detail.get("comments") or []
            )
            child_outputs = "\n".join(str(child.get("latest_deliverable") or "") for child in children)
            checks.update({
                "at_least_two_children": len(children) >= 2,
                "expected_distinct_workers": {child.get("agent_name") for child in children} == expected_workers,
                "child_outputs_contain_tokens": all(case_data[key] in child_outputs for key in ("part_a", "part_b")),
                "auto_resume_version_gte_2": system_resume and max_version >= 2,
                "no_human_resume": ledger.count("human_resume") == 0,
            })
        else:
            checks["no_child_tasks"] = not children
        accepted_goal = int(terminal_reason == "succeeded" and all(checks.values()))
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
            "checks": checks,
            "delivery": output,
            "task_ids": [root_id, *(int(child["id"]) for child in children)],
            "max_root_loop_version": max_version,
            "early_delivery": early_delivery,
            "total_tokens": usage["total_tokens"],
            "token_usage": usage,
            "wall_seconds": budget.elapsed_seconds,
            "budget": budget.snapshot(),
            "human": ledger.snapshot(),
            **vgh_measurement(accepted_goal, ledger.total_seconds),
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
            "case_data_sha256": sha256_json(case_data),
            **extra_evidence,
        })
        atomic_write_json(output_dir / "run.json", result)
        return result
    finally:
        if external is not None:
            external.__exit__(None, None, None)


def validate_suite(suite_path: Path = DEFAULT_SUITE) -> dict[str, Any]:
    suite = load_suite(suite_path)
    validated = []
    for definition in suite["cases"]:
        _, _, scenario = load_case(suite_path, definition["id"])
        if scenario["id"] == "core-v1":
            core = validate_offline(PLATFORM_ROOT / definition["scenario"])
            validated.append({"id": scenario["id"], "status": core["status"]})
            continue
        for mode in ("single", "multi"):
            data = _new_case_data(scenario["case_type"])
            prompt = build_prompt(scenario, mode, data, "offline-validation")
            if scenario["case_type"] == "external_info" and data["observation"]["value"] in prompt:
                raise ExecutionError("外部信息题不得把随机观测值写入题面")
        validated.append({"id": scenario["id"], "status": "passed"})
    general = yaml.safe_load((ROOT / "control-plane/agents/fleet-eval-general-01.yaml").read_text(encoding="utf-8"))
    agent = general["spec"]["agent"]
    if agent.get("memory_enabled") is not False or agent.get("allowed_subagents") != []:
        raise ExecutionError("fleet-eval-general-01 必须零记忆、零子 Agent")
    return {
        "status": "passed",
        "suite_id": suite["id"],
        "cases": validated,
        "required_e2e_runs": [
            {"case": case["id"], "mode": mode}
            for case in suite["cases"] for mode in case["required_modes"]
        ],
        "repeat_k": suite["repeat_k"],
        "real_tasks_started": False,
        "external_services_started": False,
    }


def collect_suite_results(
    suite: dict[str, Any],
    root: Path = ARTIFACT_ROOT,
    iteration_id: str | None = None,
) -> list[dict[str, Any]]:
    results = []
    for case in suite["cases"]:
        run_root = root / case["id"] / "runs"
        if not run_root.is_dir():
            continue
        for path in sorted(run_root.glob("*/run.json")):
            result = read_json(path)
            if result.get("scenario_id") != case["id"]:
                continue
            if iteration_id is not None and result.get("iteration_id") != iteration_id:
                continue
            results.append(result)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    run = sub.add_parser("run")
    run.add_argument("--case", required=True)
    run.add_argument("--mode", choices=("single", "multi"), required=True)
    run.add_argument("--run-id")
    run.add_argument("--iteration")
    run.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    run.add_argument("--api", default=os.environ.get("FLEET_API", "http://127.0.0.1:8003"))
    run.add_argument("--active-human-seconds", type=float, default=0)
    run.add_argument("--no-prepare", action="store_true")
    summarize = sub.add_parser("summarize")
    summarize.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    summarize.add_argument("--runs-root", type=Path, default=ARTIFACT_ROOT)
    summarize.add_argument("--iteration")
    summarize.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "validate":
        print(json.dumps(validate_suite(args.suite), ensure_ascii=False, indent=2))
        return 0
    if args.command == "summarize":
        suite = load_suite(args.suite)
        summary = summarize_suite(
            collect_suite_results(suite, args.runs_root, args.iteration), suite)
        if args.output:
            atomic_write_json(args.output, summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    _, definition, scenario = load_case(args.suite, args.case)
    if args.mode not in definition["required_modes"]:
        raise ExecutionError(
            f"{args.case}/{args.mode} 不在基础 E2E 清单；专项实验请使用独立集合配置")
    run_id = args.run_id or f"{args.case}-{uuid.uuid4().hex[:10]}"
    output = artifact_path(ARTIFACT_ROOT / args.case / "runs", f"{run_id}-{args.mode}")
    client = FleetClient(args.api, os.environ.get("FLEET_PAT") or None)
    if args.case == "core-v1":
        result = run_core_evaluation(
            client, output, args.mode,
            active_human_seconds=args.active_human_seconds,
            prepare=not args.no_prepare,
            persist=False,
            interpret=False,
        )
        result["zero_human_verified_success"] = zero_human_verified_success(result)
        result["iteration_id"] = args.iteration
        atomic_write_json(output / "run.json", result)
    else:
        result = execute_case_run(
            client, scenario, output, args.mode,
            active_human_seconds=args.active_human_seconds,
            iteration_id=args.iteration,
            prepare=not args.no_prepare,
        )
    print(json.dumps({
        "run_id": result["run_id"],
        "iteration_id": result.get("iteration_id"),
        "case": args.case,
        "mode": args.mode,
        "status": result["status"],
        "accepted_goal": result["accepted_goal"],
        "zero_human_verified_success": result["zero_human_verified_success"],
        "total_tokens": result["total_tokens"],
        "wall_seconds": result["wall_seconds"],
        "output": str(output),
        "auto_development_allowed": False,
    }, ensure_ascii=False, indent=2))
    return 0 if result["zero_human_verified_success"] == 1 else 2


if __name__ == "__main__":
    raise SystemExit(main())
