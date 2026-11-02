#!/usr/bin/env python3
"""Newline-delimited JSON-RPC mock used only by offline MCP adapter tests."""

from __future__ import annotations

import json
import os
import sys


def reply(request_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    try:
        request = json.loads(line)
    except ValueError:
        continue
    method = request.get("method")
    request_id = request.get("id")
    if request_id is None:
        continue
    if method == "initialize":
        reply(request_id, {"protocolVersion": "2024-11-05", "capabilities": {}})
    elif method == "tools/list":
        reply(request_id, {"tools": [
            {"name": "observe", "inputSchema": {"type": "object"}},
            {"name": "fail", "inputSchema": {"type": "object"}},
        ]})
    elif method == "tools/call":
        params = request.get("params") or {}
        if params.get("name") == "fail":
            reply(request_id, {"isError": True, "content": [{"type": "text", "text": "expected failure"}]})
        else:
            value = (params.get("arguments") or {}).get("value")
            reply(request_id, {"structuredContent": {"value": value, "source": os.environ.get("MOCK_SETTING", "mcp-stdio")}})
