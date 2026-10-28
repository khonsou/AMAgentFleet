"""Dispatcher 对外产物链接不得写死本地开发地址。"""

import unittest
from unittest.mock import patch

from app.dispatcher import _extract_artifacts


class ArtifactLinkTests(unittest.TestCase):
    def test_default_link_is_same_origin_and_deployment_neutral(self):
        with patch.dict("os.environ", {}, clear=True):
            links = _extract_artifacts(
                {"artifacts": ["/mnt/user-data/outputs/result.md"]},
                "4c6a2c52-16f2-4b0d-9882-4f826ea58f6d",
            )
        self.assertEqual(links, [
            "result.md: /api/console/threads/4c6a2c52-16f2-4b0d-9882-4f826ea58f6d"
            "/artifacts/mnt/user-data/outputs/result.md"
        ])
        self.assertNotIn("127.0.0.1", links[0])
        self.assertNotIn("localhost", links[0])

    def test_explicit_public_base_remains_supported(self):
        with patch.dict("os.environ", {"FLEET_ARTIFACT_LINK_BASE": "https://fleet.example.com/"}):
            links = _extract_artifacts(
                {"artifacts": ["/mnt/user-data/outputs/result.md"]},
                "4c6a2c52-16f2-4b0d-9882-4f826ea58f6d",
            )
        self.assertTrue(links[0].startswith("result.md: https://fleet.example.com/api/"))


if __name__ == "__main__":
    unittest.main()
