"""FAP 幂等键请求指纹回归：同键不同正文不得产生第二次写入。"""

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from starlette.requests import Request

from app import fap_middleware


def make_request(body: bytes) -> Request:
    request = Request({
        "type": "http", "method": "POST", "path": "/api/tasks", "query_string": b"",
        "headers": [(b"idempotency-key", b"ops-test")], "client": ("127.0.0.1", 1234),
    })
    request._body = body
    return request


class FakePool:
    def __init__(self, row):
        self.row = row

    def acquire(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def transaction(self):
        return self

    async def fetchrow(self, query, *_args):
        if query.lstrip().startswith("INSERT INTO fap_idempotency_keys"):
            return None
        return self.row


class IdempotencyTests(unittest.IsolatedAsyncioTestCase):
    async def run_middleware(self, request, row):
        call_next = AsyncMock()
        with (
            patch("app.fap_middleware.pool", AsyncMock(return_value=FakePool(row))),
            patch("app.fap_middleware._actor_identity", return_value="pat:test"),
        ):
            response = await fap_middleware.idempotency_middleware(request, call_next)
        return response, call_next

    async def test_same_request_replays_without_calling_write(self):
        body = b'{"input":"same"}'
        row = {
            "actor": "pat:test", "status_code": 201, "response_json": {"task_id": 7},
            "request_digest": fap_middleware._request_fingerprint("POST", "/api/tasks", "", body),
            "created_at": datetime.now(timezone.utc),
        }
        response, call_next = await self.run_middleware(make_request(body), row)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers["x-idempotent-replay"], "true")
        call_next.assert_not_awaited()

    async def test_same_key_different_body_is_conflict(self):
        original = b'{"input":"original"}'
        row = {
            "actor": "pat:test", "status_code": 201, "response_json": {"task_id": 7},
            "request_digest": fap_middleware._request_fingerprint("POST", "/api/tasks", "", original),
            "created_at": datetime.now(timezone.utc),
        }
        response, call_next = await self.run_middleware(make_request(b'{"input":"changed"}'), row)
        self.assertEqual(response.status_code, 409)
        call_next.assert_not_awaited()

    def test_fingerprint_covers_query_and_never_contains_body(self):
        first = fap_middleware._request_fingerprint("POST", "/api/tasks", "a=1", b"secret-body")
        second = fap_middleware._request_fingerprint("POST", "/api/tasks", "a=2", b"secret-body")
        self.assertNotEqual(first, second)
        self.assertNotIn("secret-body", first)
        self.assertEqual(len(first), 64)


if __name__ == "__main__":
    unittest.main()
