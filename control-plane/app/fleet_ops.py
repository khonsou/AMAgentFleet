"""Fleet Operations Protocol（ADR-0017）：唯一机器可读操作清单。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter

PROTOCOL_PATH = Path(__file__).resolve().parents[2] / "fleet-ops-protocol.json"
router = APIRouter(tags=["fleet-ops"])


@lru_cache(maxsize=1)
def load_operations_protocol() -> dict[str, Any]:
    with PROTOCOL_PATH.open(encoding="utf-8") as f:
        return json.load(f)


@router.get("/api/ops/protocol")
async def operations_protocol() -> dict[str, Any]:
    """返回完整操作清单；operation ID 是默认操作 Agent 的唯一调用依据。"""
    return load_operations_protocol()
