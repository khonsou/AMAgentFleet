"""Select one internal Runtime for the whole deployment (ADR-0032)."""

from __future__ import annotations

import os
from functools import lru_cache

from .base import RuntimeAdapter


@lru_cache(maxsize=1)
def get_runtime() -> RuntimeAdapter:
    name = os.environ.get("FLEET_RUNTIME", "deerflow").strip().lower()
    if name == "deerflow":
        from .deerflow import DeerFlowAdapter
        return DeerFlowAdapter()
    if name == "deepseek-thin":
        from .deepseek_thin import DeepSeekThinAdapter
        return DeepSeekThinAdapter()
    raise RuntimeError(f"未知 FLEET_RUNTIME: {name!r}；支持值为 deerflow、deepseek-thin")


def reset_runtime_for_tests() -> None:
    get_runtime.cache_clear()
