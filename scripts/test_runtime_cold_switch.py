"""冷切换预检：目标 Runtime 与活动 Loop 门闩。"""

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

MODULE_PATH = Path(__file__).resolve().with_name("runtime_cold_switch_check.py")
SPEC = importlib.util.spec_from_file_location("runtime_cold_switch_check", MODULE_PATH)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class ColdSwitchPreflightTests(unittest.IsolatedAsyncioTestCase):
    def payloads(self, runtime="deepseek-thin"):
        return {
            "/api/health": {"status": "ok"},
            "/api/runtime/capabilities": {"runtime": runtime, "capabilities": {"resume": False}},
            "/api/ops/protocol": {"operations": [{"id": "system.runtime_capabilities"}]},
        }

    async def test_matching_runtime_and_drained_pool_pass(self):
        connection = AsyncMock()
        connection.fetchval.return_value = 0
        with patch.object(module, "fetch_json", side_effect=lambda _base, path: self.payloads()[path]), \
                patch.object(module.asyncpg, "connect", AsyncMock(return_value=connection)):
            result = await module.preflight("http://cp:8003", "deepseek-thin", "postgresql://secret")
        self.assertEqual(result["runtime"], "deepseek-thin")
        connection.close.assert_awaited_once()

    async def test_runtime_mismatch_fails_before_pg(self):
        with patch.object(module, "fetch_json", side_effect=lambda _base, path: self.payloads("deerflow")[path]), \
                patch.object(module.asyncpg, "connect", AsyncMock()) as connect:
            with self.assertRaisesRegex(module.PreflightError, "Runtime 不一致"):
                await module.preflight("http://cp:8003", "deepseek-thin", "postgresql://secret")
        connect.assert_not_awaited()

    async def test_active_loops_block_switch(self):
        connection = AsyncMock()
        connection.fetchval.return_value = 2
        with patch.object(module, "fetch_json", side_effect=lambda _base, path: self.payloads()[path]), \
                patch.object(module.asyncpg, "connect", AsyncMock(return_value=connection)):
            with self.assertRaisesRegex(module.PreflightError, "仍有 2 个"):
                await module.preflight("http://cp:8003", "deepseek-thin", "postgresql://secret")
        connection.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
