"""FAP 审计透传上下文（ADR-0011 H5，QA-3 方案 a）。

写操作 body 里的 `source`/`actor` 自报字段由中间件提取进 contextvar，
`db.audit()` 落库时自动 merge 进 `audit_events.detail`——
所有现有 audit() 调用点零改动，SQL `detail->'actor'` 可查。
"""

from __future__ import annotations

import contextvars
from typing import Any

fap_source: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "fap_source", default=None
)
fap_actor: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "fap_actor", default=None
)
