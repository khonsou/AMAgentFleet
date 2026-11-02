#!/usr/bin/env python3
"""Real acceptance for automatic parent resume with an isolated temporary CP on :8004."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CP = "http://127.0.0.1:8004"
OUT = ROOT / "artifacts/platform-evals/core-v1/auto-resume/result.json"
RUN_ID = str(int(time.time()))
PROMPT = f"""【自动唤醒验收 {RUN_ID}：受控双源事实简报】
目标：最终交付不超过 600 字的事实简报，必须包含 Fleet 与 Timeline 两个来源的数字、来源 Task ID 和一句交叉结论。
受控事实（无需也不能调用外部接口）：
A. Fleet：open=4、review=5、done=6。
B. Timeline：3 个看板共 12 张卡；待执行 6、待发布 3、已发布 3。
执行要求：
1. 先读取已注册 Agent，自主至少并行创建两个不同子任务，分别核验 A、B。worker 只能使用本条受控事实，不得调用 Timeline、HTTP 或任何凭据。
2. 子任务必须自包含，给出上述数字，要求各自计算合计/占比并注明来源。不得索取密码或 base URL。
3. 子任务创建后，你先交付一次简短阶段性说明并结束当前回合；系统应在全部子任务交付后自动唤醒你。不得由用户再次发送继续指令。
4. 自动续跑后读取真实子任务 OUTPUT，核对 A/B 数字，纠正矛盾并交付最终简报。不得重复派发，不得把已创建当已交付。
5. 不验收、不关闭、不修改任何既有任务。"""


def call(path: str, body: dict | None = None, timeout: int = 60):
    request = urllib.request.Request(
        f"{CP}{path}",
        data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def tasks() -> list[dict]:
    return call("/api/tasks?limit=200&offset=0")


async def prepare() -> None:
    from app.db import init_db
    from app import registry

    await init_db()
    for name in ("fleet-cmd-01", "fleet-collect-01", "fleet-analyze-01", "smoke-test-register"):
        await registry.ensure_agent(name)


def start_gateway() -> subprocess.Popen:
    source = ROOT / "spike/deer-flow/config.yaml"
    config_path = ROOT / "artifacts/platform-evals/gateway-eval-config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config = yaml.safe_load(source.read_text(encoding="utf-8"))
    config.setdefault("scheduler", {})["enabled"] = False
    config["recursion_limit"] = 300
    config["max_recursion_limit"] = 300
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{ROOT}:{ROOT / 'spike/deer-flow/backend'}"
    env["DEER_FLOW_PROJECT_ROOT"] = str(ROOT / "spike/deer-flow")
    env["DEER_FLOW_HOME"] = str(ROOT / "spike/deer-flow/backend/.deer-flow")
    env["DEER_FLOW_CONFIG_PATH"] = str(config_path)
    env["DEER_FLOW_AUTH_DISABLED"] = "1"
    env["FLEET_CONTROL_PLANE"] = CP
    env["NO_PROXY"] = "localhost,127.0.0.1,::1"
    env["no_proxy"] = "localhost,127.0.0.1,::1"
    uv = os.environ.get("UV_BIN") or shutil.which("uv")
    if not uv:
        for candidate in (Path.home() / ".local/bin/uv", Path("/opt/homebrew/bin/uv"), Path("/usr/local/bin/uv")):
            if candidate.is_file():
                uv = str(candidate)
                break
    if not uv:
        raise RuntimeError("uv not found; set UV_BIN to the executable path")
    (ROOT / "console/logs").mkdir(parents=True, exist_ok=True)
    log = open(ROOT / "console/logs/platform-eval-gateway-8005.log", "w")
    return subprocess.Popen(
        [uv, "run", "--no-sync", "uvicorn",
         "app.gateway.app:app", "--host", "127.0.0.1", "--port", "8005"],
        cwd=ROOT / "spike/deer-flow/backend", env=env,
        stdout=log, stderr=subprocess.STDOUT,
    )


def start_server() -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{ROOT}:{ROOT / 'control-plane'}"
    env["FLEET_CONTROL_PLANE"] = CP
    env["FLEET_CONTROL_PLANE_SELF_URL"] = CP
    env["FLEET_GATEWAY_URL"] = "http://127.0.0.1:8005"
    code = (
        "import uvicorn; "
        "from app.singleton import SingletonGate; "
        "import app.singleton as singleton; "
        "singleton._gate = SingletonGate(lock_key=818181); "
        "from app.main import app; "
        "uvicorn.run(app, host='127.0.0.1', port=8004, log_level='warning')"
    )
    log = open(ROOT / "console/logs/platform-eval-8004.log", "w")
    return subprocess.Popen(
        [str(ROOT / "spike/deer-flow/backend/.venv/bin/python"), "-c", code],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
    )


def stop_server(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    asyncio.run(prepare())
    gateway = start_gateway()
    process = start_server()
    root_id = None
    result = {"status": "running"}
    try:
        gateway_deadline = time.monotonic() + 30
        while time.monotonic() < gateway_deadline:
            try:
                with urllib.request.urlopen("http://127.0.0.1:8005/health", timeout=2) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("Temporary gateway did not become healthy")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                if call("/api/health"):
                    break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("Temporary control plane did not become healthy")

        created = call("/api/agents/fleet-cmd-01/dispatch", {"input": PROMPT, "source": "web"})
        root_id = created["task_id"]
        result = {"root_task_id": root_id, "status": "running", "history": []}
        started = time.monotonic()
        signature = None
        injected_early = False
        while time.monotonic() - started < 280:
            rows = tasks()
            root = next(row for row in rows if row["id"] == root_id)
            children = [row for row in rows if row.get("parent_task_id") == root_id]
            current = {
                "root_status": root["status"],
                "root_pool_status": root["pool_status"],
                "root_loop_status": root.get("latest_loop_status"),
                "children": [[row["id"], row["agent_name"], row["status"]] for row in children],
            }
            if current != signature:
                result["history"].append(current)
                print(json.dumps(current, ensure_ascii=False), flush=True)
                signature = current
            if (children and not injected_early and root.get("latest_loop_status") == "running"
                    and all(child["status"] not in ("success", "error", "rejected") for child in children)):
                call(f"/api/loops/{root['latest_loop_id']}/deliver", {
                    "content": "阶段性说明（测试夹具：父任务先于子任务交付）",
                    "author": "platform-eval-fixture",
                })
                injected_early = True
                result["injected_early"] = True
                print(json.dumps({"injected_early": True, "root_id": root_id}), flush=True)
            detail = call(f"/api/tasks/{root_id}")
            max_version = max((loop.get("version", 1) for loop in detail.get("loops") or []), default=1)
            if (root["status"] in ("success", "error", "rejected") and max_version >= 2
                    and root.get("latest_loop_status") in ("delivered", "accepted")):
                result.update({
                    "status": "finished",
                    "root": root,
                    "detail": detail,
                    "children": children,
                    "elapsed_seconds": time.monotonic() - started,
                })
                break
            time.sleep(1)
        else:
            result["status"] = "timeout"
            result["root"] = next(row for row in tasks() if row["id"] == root_id)
            result["children"] = [row for row in tasks() if row.get("parent_task_id") == root_id]
    finally:
        stop_server(process)
        stop_server(gateway)
        if root_id is not None:
            OUT.parent.mkdir(parents=True, exist_ok=True)
            OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n")

    print(f"ARTIFACT={OUT}")
    if result.get("status") != "finished":
        return 2
    loops = result["detail"].get("loops") or []
    max_version = max((loop.get("version", 1) for loop in loops), default=1)
    output = result["root"].get("latest_deliverable") or ""
    checks = {
        "at_least_two_children": len(result["children"]) >= 2,
        "auto_resume_version_increased": max_version >= 2,
        "contains_card_total": "12" in output,
        "contains_fleet_states": all(value in output for value in ("4", "5", "6")),
    }
    print(json.dumps({"checks": checks, "max_loop_version": max_version, "output": output}, ensure_ascii=False, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
