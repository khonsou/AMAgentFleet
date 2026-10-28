"""在锁定的 DeerFlow 配置上幂等合并 Fleet 自有工具（启动时运行）。"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

FLEET_GROUPS = ("fleet", "fleet:ops")
FLEET_TOOLS = {
    "agent_spawn": {"group": "fleet", "use": "fleet_tools:agent_spawn"},
    "agent_message": {"group": "fleet", "use": "fleet_tools:agent_message"},
    "fleet_ops_request": {"group": "fleet:ops", "use": "fleet_tools:fleet_ops_request"},
}


def prepare(source: Path, target: Path) -> None:
    with source.open(encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    groups = config.setdefault("tool_groups", [])
    existing_groups = {item.get("name") for item in groups if isinstance(item, dict)}
    groups.extend({"name": name} for name in FLEET_GROUPS if name not in existing_groups)

    tools = config.setdefault("tools", [])
    by_name = {
        item.get("name"): item
        for item in tools
        if isinstance(item, dict) and item.get("name")
    }
    for name, definition in FLEET_TOOLS.items():
        entry = by_name.get(name)
        if entry is None:
            tools.append({"name": name, **definition})
        else:
            entry.update(definition)

    with target.open("w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)


if __name__ == "__main__":
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/app/config.fleet.yaml")
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/app/backend/config.yaml")
    prepare(source, target)
