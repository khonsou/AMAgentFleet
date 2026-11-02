"""Run: python -m unittest -v test_authjwt (from console/backend)."""

import time
import unittest
from unittest.mock import AsyncMock, patch

import jwt
import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import authjwt


class DaoAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.public_key = cls.private_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

    def setUp(self):
        authjwt._group_cache.clear()
        self.addCleanup(authjwt._group_cache.clear)
        config = patch.multiple(
            authjwt, _ENABLED=True, _ISSUER="dao-test", _PUBLIC_KEY=self.public_key, _GROUP_ID=2,
        )
        config.start()
        self.addCleanup(config.stop)
        # 默认：coin 分组接口返回内部员工组 2，个别用例再改写
        self.real_fetch = authjwt._fetch_group_ids
        self.fetch = AsyncMock(return_value=[1, 17, 2])
        fetcher = patch.object(authjwt, "_fetch_group_ids", self.fetch)
        fetcher.start()
        self.addCleanup(fetcher.stop)
        app = FastAPI()
        app.add_middleware(authjwt.DaoAuthMiddleware)

        async def protected(request: Request):
            return {"user_id": request.state.dao_user_id}

        for path in ("/api/console/agents", "/api/console/tasks"):
            app.add_api_route(path, protected)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def token(self, _omit=(), **overrides):
        claims = {
            "iss": "dao-test", "aud": "am-agent-fleet", "exp": int(time.time()) + 120,
            "user_id": 42, "client_id": "am-agent-fleet", "token_type": "access",
        }
        claims.update(overrides)
        for name in _omit:
            claims.pop(name)
        return jwt.encode(claims, self.private_key, algorithm="RS256")

    def test_fleet_access_token_reaches_both_endpoints(self):
        token = self.token()
        for path in ("/api/console/agents", "/api/console/tasks?limit=200"):
            with self.subTest(path=path):
                response = self.client.get(path, headers={"Authorization": f"Bearer {token}"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"user_id": 42})
        self.fetch.assert_awaited_once_with(token)

    def test_rejects_token_outside_inhouse_group(self):
        self.fetch.return_value = [1, 17]
        response = self.client.get(
            "/api/console/agents",
            headers={"Authorization": f"Bearer {self.token()}"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"detail": "仅限 inhouse 员工访问 Console"})

    def test_role_claim_does_not_bypass_group_check(self):
        self.fetch.return_value = [1, 17]
        response = self.client.get(
            "/api/console/agents",
            headers={"Authorization": f"Bearer {self.token(role='inhouse')}"},
        )
        self.assertEqual(response.status_code, 403)

    def test_group_id_must_be_an_exact_integer(self):
        self.fetch.return_value = [1, "2", True]
        response = self.client.get(
            "/api/console/agents",
            headers={"Authorization": f"Bearer {self.token()}"},
        )
        self.assertEqual(response.status_code, 403)

    def test_fails_closed_when_group_api_unavailable(self):
        self.fetch.side_effect = authjwt.InhouseCheckUnavailable("coin down")
        token = self.token()
        for _ in range(2):
            response = self.client.get(
                "/api/console/agents", headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {"detail": "员工身份校验服务不可用，请稍后重试"})
        # 失败结果不缓存：两次请求各自回源
        self.assertEqual(self.fetch.await_count, 2)

    def test_caches_group_result_within_ttl(self):
        token = self.token()
        for _ in range(3):
            response = self.client.get(
                "/api/console/agents", headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.fetch.await_count, 1)

    def test_group_api_request_forwards_the_user_token(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["authorization"] = request.headers.get("authorization")
            return httpx.Response(200, json={"ids": [1, 17, 2]})

        real_client = httpx.AsyncClient
        with patch.object(authjwt, "_fetch_group_ids", self.real_fetch), patch.object(
            authjwt.httpx, "AsyncClient",
            lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
        ):
            token = self.token()
            response = self.client.get(
                "/api/console/agents", headers={"Authorization": f"Bearer {token}"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen["authorization"], f"Bearer {token}")

    def test_rejects_invalid_identity_and_token_binding(self):
        for overrides in (
            {"aud": "timeline"}, {"aud": None}, {"iss": "other"},
            {"exp": int(time.time()) - 60}, {"exp": None},
            {"token_type": "refresh"}, {"token_type": None},
            {"client_id": "timeline"}, {"user_id": True}, {"user_id": None},
        ):
            with self.subTest(overrides=overrides):
                response = self.client.get(
                    "/api/console/agents",
                    headers={"Authorization": f"Bearer {self.token(**overrides)}"},
                )
                self.assertEqual(response.status_code, 401)
        self.fetch.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()