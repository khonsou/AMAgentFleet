#!/usr/bin/env python3
"""Apply Fleet's DeerFlow tool_groups null-reset fix idempotently."""

from __future__ import annotations

import os
from pathlib import Path

TARGET = Path(os.environ.get("DEERFLOW_AGENTS_PATH", "/app/backend/app/gateway/routers/agents.py"))
OLD = '''            new_tool_groups = body.tool_groups if "tool_groups" in fields_set else agent_cfg.tool_groups
            if new_tool_groups is not None:
                updated["tool_groups"] = new_tool_groups'''
NEW = '''            new_tool_groups = body.tool_groups if "tool_groups" in fields_set else agent_cfg.tool_groups
            # null = inherit all and clear a stale whitelist; [] = explicitly zero tools.
            updated["tool_groups"] = new_tool_groups'''


def main() -> None:
    text = TARGET.read_text(encoding="utf-8")
    if NEW in text:
        print("deerflow tool_groups null-reset already present")
        return
    if OLD not in text:
        raise SystemExit("deerflow tool_groups update block not found; review base image before patching")
    updated = text.replace(OLD, NEW, 1)
    TARGET.write_text(updated, encoding="utf-8")
    print("applied deerflow tool_groups null-reset")


if __name__ == "__main__":
    main()
