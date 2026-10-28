"""ADR-0012：PostgreSQL session 级常驻锁，限制后台循环单实例运行。"""

from __future__ import annotations

import logging

import asyncpg

from .db import PG_DSN

logger = logging.getLogger("fleet.singleton")

# 8 字节 "AMFLEET1"；session 锁跨控制面进程/容器互斥，连接断开自动释放。
ADVISORY_LOCK_KEY = 0x414D464C45455431


class SingletonGate:
    """每个实例持一个 Gate；独立连接避免占用业务连接池。"""

    def __init__(self, *, lock_key: int = ADVISORY_LOCK_KEY):
        self.lock_key = lock_key
        self._conn: asyncpg.Connection | None = None

    @property
    def acquired(self) -> bool:
        return self._conn is not None

    async def acquire(self) -> bool:
        if self._conn is not None:
            return True
        conn = await asyncpg.connect(PG_DSN)
        try:
            locked = await conn.fetchval("SELECT pg_try_advisory_lock($1)", self.lock_key)
        except Exception:  # noqa: BLE001
            await conn.close()
            raise
        if not locked:
            await conn.close()
            logger.warning("另一控制面实例持有单例门闩；本实例后台循环保持停用")
            return False
        self._conn = conn
        logger.info("控制面单例门闩已获取")
        return True

    async def release(self) -> None:
        conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            await conn.execute("SELECT pg_advisory_unlock($1)", self.lock_key)
        except Exception:  # noqa: BLE001
            logger.exception("单例门闩显式释放失败；关闭连接后由 PostgreSQL 回收")
        finally:
            await conn.close()


_gate = SingletonGate()


async def acquire() -> bool:
    return await _gate.acquire()


async def release() -> None:
    await _gate.release()
