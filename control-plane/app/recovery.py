"""Fleet Run 异常分类与 Runtime 恢复引用读取。"""

from __future__ import annotations

import httpx

from .runtime import get_runtime
from .runtime.base import classify_runtime_failure

TRANSIENT_HTTP_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}


def classify_failure(error: str, *, status_code: int | None = None,
                     transport: bool = False) -> str:
    """保留现有分类 API，底层使用 Runtime 统一规则。"""
    return classify_runtime_failure(error, status_code=status_code, transport=transport)


async def latest_checkpoint(thread_id: str) -> str | None:
    """读取当前 Runtime 的不透明恢复引用；无法读取时由调用方决定降级。"""
    return await get_runtime().latest_checkpoint(thread_id, client_factory=httpx.AsyncClient)
