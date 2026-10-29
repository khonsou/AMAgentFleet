"""Gateway 模型目录与 Loop 级模型解析（ADR-0028）。

协议只接受 Gateway 已登记的 Profile 名；API Key 与 Provider 配置始终留在
Gateway 管理面。每次 agent Loop 创建时保存不含秘密的模型快照。
"""

from __future__ import annotations

from typing import Any

import httpx

from .db import GATEWAY
from .registry import RegistryError, load_yaml_doc


class ModelError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message


async def list_profiles() -> list[dict[str, Any]]:
    """读取实时目录（含 Managed Models），不返回 api_key/base_url 等敏感配置。"""
    try:
        async with httpx.AsyncClient(base_url=GATEWAY, timeout=15, trust_env=False) as client:
            response = await client.get("/api/models")
    except httpx.HTTPError as exc:
        raise ModelError(503, "Gateway 模型目录暂不可用") from exc
    if response.status_code != 200:
        raise ModelError(502, f"Gateway 模型目录返回 {response.status_code}")
    models = response.json().get("models") or []
    if not models:
        raise ModelError(503, "Gateway 未配置可用模型 Profile")
    return [
        {
            "name": item["name"],
            "model": item["model"],
            "display_name": item.get("display_name") or item["name"],
            "description": item.get("description"),
            "supports_thinking": bool(item.get("supports_thinking")),
            "supports_reasoning_effort": bool(item.get("supports_reasoning_effort")),
        }
        for item in models
    ]


def _agent_policy(agent_name: str) -> tuple[str | None, list[str] | None]:
    try:
        doc = load_yaml_doc(agent_name)
    except RegistryError as exc:
        if exc.status == 404:
            return None, None
        raise
    agent = (doc.get("spec") or {}).get("agent") or {}
    policy = doc.get("model_policy") or {}
    allowed = policy.get("allowed_profiles")
    if allowed is not None:
        if not isinstance(allowed, list) or not all(isinstance(item, str) and item for item in allowed):
            raise ModelError(422, f"agent '{agent_name}' 的 model_policy.allowed_profiles 必须是非空字符串数组或 null")
        allowed = list(dict.fromkeys(allowed))
    return agent.get("model"), allowed


async def resolve_profile(
    agent_name: str,
    requested: str | None,
    *,
    selected_by: str,
) -> tuple[str, dict[str, Any], str]:
    """解析实例显式值 → Agent 默认 → Gateway 默认，并校验白名单。"""
    agent_default, allowed = _agent_policy(agent_name)
    profile = requested or agent_default
    source = selected_by if requested else ("agent_default" if agent_default else "system_default")
    profiles = await list_profiles()
    by_name = {item["name"]: item for item in profiles}
    profile = profile or profiles[0]["name"]
    selected = by_name.get(profile)
    if selected is None:
        raise ModelError(422, f"未登记的模型 Profile：{profile}")
    if allowed is not None and profile not in allowed:
        raise ModelError(403, f"agent '{agent_name}' 不允许使用模型 Profile：{profile}")
    snapshot = {
        "profile": selected["name"],
        "model": selected["model"],
        "display_name": selected["display_name"],
        "source": source,
    }
    return profile, snapshot, source
