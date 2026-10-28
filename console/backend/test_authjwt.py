"""Run: python -m unittest -v test_authjwt (from console/backend)."""

import time
import unittest
from unittest.mock import patch

import jwt
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
        config = patch.multiple(
            authjwt, _ENABLED=True, _ISSUER="dao-test", _PUBLIC_KEY=self.public_key,
        )
        config.start()
        self.addCleanup(config.stop)
        app = FastAPI()
        app.add_middleware(authjwt.DaoAuthMiddleware)

        async def protected(request: Request):
            return {"user_id": request.state.dao_user_id}

        for path in ("/api/console/agents", "/api/console/tasks"):
            app.add_api_route(path, protected)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def token(self, **overrides):
        claims = {
            "iss": "dao-test", "aud": "am-agent-fleet", "exp": int(time.time()) + 120,
            "user_id": 42, "client_id": "am-agent-fleet", "token_type": "access",
        }
        claims.update(overrides)
        return jwt.encode(claims, self.private_key, algorithm="RS256")

    def test_fleet_access_token_reaches_both_endpoints(self):
        token = self.token()
        for path in ("/api/console/agents", "/api/console/tasks?limit=200"):
            with self.subTest(path=path):
                response = self.client.get(path, headers={"Authorization": f"Bearer {token}"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"user_id": 42})

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


if __name__ == "__main__":
    unittest.main()
