"""ADR-0012：FAP 幂等占位行的并发与故障语义。"""

import asyncio
import os
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import asyncpg
from fastapi.responses import JSONResponse
from starlette.requests import Request

from app import db, fap_middleware


def make_request(body: bytes, key: str = "same-key") -> Request:
    request = Request({
        "type": "http", "method": "POST", "path": "/api/tasks", "query_string": b"",
        "headers": [(b"idempotency-key", key.encode())], "client": ("127.0.0.1", 1234),
    })
    request._body = body
    return request


@unittest.skipUnless(os.environ.get("FLEET_TEST_PG_DSN"), "未配置 FLEET_TEST_PG_DSN")
class PostgresIdempotencyAtomicityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schema = f"test_fap_idem_{uuid.uuid4().hex}"
        dsn = os.environ["FLEET_TEST_PG_DSN"]
        self.admin = await asyncpg.connect(dsn)
        await self.admin.execute(f'CREATE SCHEMA "{self.schema}"')
        self.db = await asyncpg.create_pool(
            dsn, min_size=1, max_size=20,
            server_settings={"search_path": self.schema},
        )
        async with self.db.acquire() as conn:
            await conn.execute("""
                CREATE TABLE fap_idempotency_keys (
                    key TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    method TEXT NOT NULL,
                    path TEXT NOT NULL,
                    request_digest TEXT NOT NULL DEFAULT '',
                    status_code INTEGER,
                    response_json JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (key, actor)
                )
            """)
        self.patches = [
            patch("app.fap_middleware.pool", AsyncMock(return_value=self.db)),
            patch("app.fap_middleware._actor_identity", return_value="pat:test"),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in self.patches:
            item.stop()
        await self.db.close()
        await self.admin.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
        await self.admin.close()

    async def invoke(self, call_next, body=b'{"input":"same"}'):
        return await fap_middleware.idempotency_middleware(make_request(body), call_next)

    async def row_count(self):
        async with self.db.acquire() as conn:
            return await conn.fetchval("SELECT count(*) FROM fap_idempotency_keys")

    async def test_concurrent_same_key_executes_only_once(self):
        calls = 0
        placeholder_seen = []

        async def call_next(_request):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.05)
            async with self.db.acquire() as conn:
                placeholder_seen.append(await conn.fetchval("""
                    SELECT status_code IS NULL AND response_json IS NULL
                    FROM fap_idempotency_keys WHERE key='same-key'
                """))
            await asyncio.sleep(0.05)
            return JSONResponse({"task_id": 7}, status_code=201)

        responses = await asyncio.gather(*(self.invoke(call_next) for _ in range(12)))

        self.assertEqual(calls, 1)
        self.assertEqual(placeholder_seen, [True])
        self.assertEqual(await self.row_count(), 1)
        self.assertTrue(all(r.status_code in (201, 409) for r in responses))
        self.assertEqual(sum(r.status_code == 201 for r in responses), 1)

        replay = await self.invoke(AsyncMock())
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(replay.headers["x-idempotent-replay"], "true")

    async def test_pending_same_request_is_rejected_without_second_write(self):
        body = b'{"input":"same"}'
        digest = fap_middleware._request_fingerprint("POST", "/api/tasks", "", body)
        async with self.db.acquire() as conn:
            await conn.execute("""
                INSERT INTO fap_idempotency_keys
                    (key, actor, method, path, request_digest)
                VALUES ('same-key', 'pat:test', 'POST', '/api/tasks', $1)
            """, digest)
        call_next = AsyncMock()

        response = await self.invoke(call_next, body)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.headers["retry-after"], "1")
        call_next.assert_not_awaited()

    async def test_server_failure_releases_placeholder_for_retry(self):
        failed = await self.invoke(AsyncMock(return_value=JSONResponse(
            {"detail": "temporary"}, status_code=500)))
        self.assertEqual(failed.status_code, 500)
        self.assertEqual(await self.row_count(), 0)

        succeeded = await self.invoke(AsyncMock(return_value=JSONResponse(
            {"task_id": 8}, status_code=201)))
        self.assertEqual(succeeded.status_code, 201)
        self.assertEqual(await self.row_count(), 1)

    async def test_route_exception_releases_placeholder(self):
        call_next = AsyncMock(side_effect=RuntimeError("route failed"))
        with self.assertRaisesRegex(RuntimeError, "route failed"):
            await self.invoke(call_next)
        self.assertEqual(await self.row_count(), 0)

    async def test_expired_placeholder_can_be_reclaimed(self):
        body = b'{"input":"same"}'
        digest = fap_middleware._request_fingerprint("POST", "/api/tasks", "", body)
        async with self.db.acquire() as conn:
            await conn.execute("""
                INSERT INTO fap_idempotency_keys
                    (key, actor, method, path, request_digest, created_at)
                VALUES ('same-key', 'pat:test', 'POST', '/api/tasks', $1,
                        now() - interval '25 hours')
            """, digest)

        response = await self.invoke(AsyncMock(return_value=JSONResponse(
            {"task_id": 9}, status_code=201)))

        self.assertEqual(response.status_code, 201)
        self.assertEqual(await self.row_count(), 1)
        async with self.db.acquire() as conn:
            status_code = await conn.fetchval(
                "SELECT status_code FROM fap_idempotency_keys WHERE key='same-key'")
        self.assertEqual(status_code, 201)


@unittest.skipUnless(os.environ.get("FLEET_TEST_PG_DSN"), "未配置 FLEET_TEST_PG_DSN")
class PostgresIdempotencyMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_completed_rows_survive_placeholder_migration(self):
        schema = f"test_fap_migration_{uuid.uuid4().hex}"
        dsn = os.environ["FLEET_TEST_PG_DSN"]
        admin = await asyncpg.connect(dsn)
        database = None
        try:
            await admin.execute(f'CREATE SCHEMA "{schema}"')
            database = await asyncpg.create_pool(
                dsn, min_size=1, max_size=5,
                server_settings={"search_path": schema},
            )
            async with database.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE personal_access_tokens (id TEXT PRIMARY KEY);
                    CREATE TABLE fap_idempotency_keys (
                        key TEXT NOT NULL,
                        actor TEXT NOT NULL,
                        method TEXT NOT NULL,
                        path TEXT NOT NULL,
                        request_digest TEXT NOT NULL DEFAULT '',
                        status_code INTEGER NOT NULL,
                        response_json JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        PRIMARY KEY (key, actor)
                    );
                    INSERT INTO fap_idempotency_keys
                        (key, actor, method, path, status_code, response_json)
                    VALUES ('old', 'pat:test', 'POST', '/api/tasks', 201, '{"task_id":1}');
                """)
            with patch("app.db.pool", AsyncMock(return_value=database)):
                await db.init_db()
            async with database.acquire() as conn:
                await conn.execute("""
                    INSERT INTO fap_idempotency_keys
                        (key, actor, method, path)
                    VALUES ('pending', 'pat:test', 'POST', '/api/tasks')
                """)
                old = await conn.fetchrow(
                    "SELECT status_code, response_json FROM fap_idempotency_keys WHERE key='old'")
            self.assertEqual(old["status_code"], 201)
            import json
            self.assertEqual(json.loads(old["response_json"]), {"task_id": 1})
        finally:
            if database is not None:
                await database.close()
            await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
            await admin.close()


if __name__ == "__main__":
    unittest.main()
