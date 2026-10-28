#!/usr/bin/env python3
"""幂等激活 Fleet Operations Protocol 与默认操作 Agent（ADR-0017）。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control-plane"))
from app import registry  # noqa: E402

CP = os.environ.get("FLEET_CONTROL_PLANE", "http://127.0.0.1:8003")
GATEWAY = os.environ.get("FLEET_GATEWAY_URL", "http://127.0.0.1:8001")
NAME = "fleet-ops-01"


def sha256_doc(doc: dict) -> str:
    blob = json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()


def activate() -> dict:
    with (ROOT / "control-plane/agents/fleet-ops-01.yaml").open(encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    with (ROOT / "fleet-ops-protocol.json").open(encoding="utf-8") as f:
        protocol = json.load(f)

    with httpx.Client(timeout=60, trust_env=False) as client:
        for base, path in ((CP, "/api/health"), (GATEWAY, "/health")):
            response = client.get(base + path)
            response.raise_for_status()

        remote_protocol = client.get(CP + "/api/ops/protocol")
        remote_protocol.raise_for_status()
        assert remote_protocol.json()["operations"] == protocol["operations"], "运行中协议与仓库不一致"
        meta = client.get(CP + "/api/meta")
        meta.raise_for_status()
        assert meta.json()["operations_protocol"] == "/api/ops/protocol"

        detail = client.get(f"{CP}/api/agents/{NAME}")
        detail.raise_for_status()
        current = detail.json().get("registry")
        action = "unchanged"
        if current is None:
            # 系统 Agent 不能走日常管理 API；激活脚本使用版本化部署原语（ADR-0018）。
            asyncio.run(registry.apply_agent(NAME, doc))
            action = "created"
        elif current.get("yaml_sha256") != sha256_doc(doc):
            asyncio.run(registry.apply_agent(NAME, doc))
            action = "updated"

        deployed = client.get(f"{CP}/api/agents/{NAME}")
        deployed.raise_for_status()
        payload = deployed.json()
        assert payload["registry"] is not None, "Control Plane Registry 未登记"
        assert payload["registry"]["yaml_sha256"] == sha256_doc(doc), "Registry 配置摘要漂移"
        agent = payload["yaml"]["spec"]["agent"]
        assert agent["tool_groups"] == ["fleet:ops"]
        assert agent["mcp_plugins"] == []
        assert agent["allowed_subagents"] == []
        assert agent["memory_enabled"] is False

        gateway = client.get(f"{GATEWAY}/api/agents/{NAME}")
        gateway.raise_for_status()
        gateway_agent = gateway.json()
        assert gateway_agent["tool_groups"] == ["fleet:ops"], gateway_agent
        assert gateway_agent["mcp_plugins"] == [], gateway_agent
        assert gateway_agent["allowed_subagents"] == [], gateway_agent
        # Gateway 响应模型当前不回显 memory_enabled；其权威值已在上方完整 YAML 校验。

        ops_read = client.post(
            GATEWAY + "/api/runs/wait",
            json={
                "assistant_id": NAME,
                "input": {"messages": [{"role": "user", "content": "只读取当前 Fleet 协议版本和健康状态，不执行任何写操作。"}]},
                "config": {"configurable": {"thread_id": "fleet-ops-activation"}},
            },
            timeout=180,
        )
        ops_read.raise_for_status()
        run_result = ops_read.json()
        assert run_result.get("status") != "error", run_result.get("error")
        messages = run_result.get("messages") or []
        assert any(m.get("type") == "ai" and m.get("content") for m in messages), "真实只读调用没有 AI 交付"
        tool_messages = [m for m in messages if m.get("type") == "tool"]
        assert any(m.get("name") == "fleet_ops_request" for m in tool_messages), "未实际调用协议工具"

    return {
        "status": "ok",
        "action": action,
        "agent": NAME,
        "operations": len(protocol["operations"]),
        "protocol": "/api/ops/protocol",
        "gateway_agent": gateway_agent["name"],
        "read_smoke_thread": "fleet-ops-activation",
    }


if __name__ == "__main__":
    print(json.dumps(activate(), ensure_ascii=False, indent=2))
