"""Agent 模板：预置 agent YAML（agents/templates/*.yaml），占位符 {{name}}/{{displayName}}/{{owner}}。

用户从模板创建 agent 时只需填名称/负责人，接入纪律（工具白名单、SOUL、预算）
由模板固化——业务接入的"最后一公里"不靠用户手写 YAML。
"""

from __future__ import annotations

import glob
import os
import re
from typing import Any

import yaml

TEMPLATE_DIR = os.environ.get(
    "FLEET_TEMPLATE_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ), "agents", "templates"),
)

_PLACEHOLDER = re.compile(r"\{\{\s*(name|displayName|owner|costCenter|egressDomain)\s*\}\}")


def list_templates() -> list[dict[str, Any]]:
    os.makedirs(TEMPLATE_DIR, exist_ok=True)
    out = []
    for path in sorted(glob.glob(os.path.join(TEMPLATE_DIR, "*.yaml"))):
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        meta = doc.get("metadata") or {}
        out.append({
            "id": os.path.splitext(os.path.basename(path))[0],
            "name": meta.get("name") or "",
            "displayName": meta.get("displayName") or meta.get("display_name") or "",
            "description": (doc.get("spec", {}).get("agent", {}) or {}).get("description", ""),
        })
    return out


def render_template(template_id: str, values: dict[str, str]) -> dict[str, Any]:
    """占位符替换（name 同时规范化）。未提供的占位符留空字符串。"""
    path = os.path.join(TEMPLATE_DIR, f"{template_id}.yaml")
    if not os.path.exists(path):
        raise FileNotFoundError(f"template not found: {template_id}")
    with open(path, encoding="utf-8") as f:
        text = f.read()

    name = re.sub(r"[^a-z0-9-]+", "-", (values.get("name") or "").strip().lower()).strip("-")
    repl = {
        "name": name,
        "displayName": values.get("displayName") or values.get("display_name") or name,
        "owner": values.get("owner") or "",
        "costCenter": values.get("costCenter") or values.get("cost_center") or "",
        "egressDomain": (values.get("egressDomain") or values.get("egress_domain") or "").strip(),
    }
    text = _PLACEHOLDER.sub(lambda m: repl.get(m.group(1), ""), text)
    doc = yaml.safe_load(text)
    doc.setdefault("metadata", {})["name"] = name
    # 未提供的占位符（如 egressDomain）渲染为空串，从列表里剔除，避免空域名入白名单
    egress = doc.get("egress")
    if isinstance(egress, list):
        doc["egress"] = [e for e in egress if isinstance(e, str) and e.strip()]
    return doc
