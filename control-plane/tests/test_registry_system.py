"""系统内置 Agent 的日常管理面保护（ADR-0018）。"""

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "control-plane"))

from app import main, registry  # noqa: E402


class SystemAgentProtectionTests(unittest.IsolatedAsyncioTestCase):
    async def assert_locked(self, awaitable):
        with self.assertRaises(HTTPException) as caught:
            await awaitable
        self.assertEqual(caught.exception.status_code, 403)
        self.assertIn("系统内置 Agent", caught.exception.detail)

    async def test_create_cannot_claim_system_name(self):
        body = main.AgentUpsertBody(yaml={"metadata": {"name": registry.SYSTEM_AGENT_NAME}})
        with patch("app.main.registry.apply_agent", new=AsyncMock()) as apply_agent:
            await self.assert_locked(main.create_agent(body))
        apply_agent.assert_not_awaited()

    async def test_update_delete_stop_and_start_are_locked(self):
        body = main.AgentUpsertBody(yaml={"metadata": {"name": registry.SYSTEM_AGENT_NAME}})
        calls = [
            main.update_agent(registry.SYSTEM_AGENT_NAME, body),
            main.delete_agent(registry.SYSTEM_AGENT_NAME),
            main.stop_agent(registry.SYSTEM_AGENT_NAME),
            main.start_agent(registry.SYSTEM_AGENT_NAME),
        ]
        for call in calls:
            await self.assert_locked(call)

    async def test_versioned_deployment_primitive_remains_available(self):
        doc = {"metadata": {"name": registry.SYSTEM_AGENT_NAME}}
        row = {"name": registry.SYSTEM_AGENT_NAME, "config_version": 2}
        with patch("app.registry.write_yaml_doc"), \
                patch("app.registry.sync_to_gateway", new=AsyncMock()), \
                patch("app.registry.upsert_registry_row", new=AsyncMock()), \
                patch("app.registry.pool", new=AsyncMock()) as db_pool:
            conn = AsyncMock()
            conn.fetchrow.return_value = row
            acquire = type("Acquire", (), {
                "__aenter__": AsyncMock(return_value=conn), "__aexit__": AsyncMock(),
            })()
            db_pool.return_value.acquire = lambda: acquire
            result = await registry.apply_agent(registry.SYSTEM_AGENT_NAME, doc)
        self.assertEqual(result["config_version"], 2)


if __name__ == "__main__":
    unittest.main()
