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

    async def test_startup_restores_system_agent_before_credential(self):
        ensure_agent = AsyncMock()
        ensure_credential = AsyncMock()
        with patch("app.main.init_db", new=AsyncMock()), \
                patch("app.main.singleton.acquire", new=AsyncMock(return_value=True)), \
                patch("app.main.dispatcher.recover_claimed", new=AsyncMock(return_value=0)), \
                patch("app.main.registry.ensure_system_agent", new=ensure_agent), \
                patch("app.main.ops_credentials.ensure_system_credential", new=ensure_credential), \
                patch("app.main.ops_credentials.check_system_credential_readable", new=AsyncMock()), \
                patch("app.main.scheduler.sync_loop", new=AsyncMock()), \
                patch("app.main.inbox.flush_loop", new=AsyncMock()):
            await main.startup()
            await main.shutdown()
        ensure_agent.assert_awaited_once_with()
        ensure_credential.assert_awaited_once_with()

    async def test_system_agent_always_syncs_gateway_without_version_churn(self):
        doc = {"metadata": {"name": registry.SYSTEM_AGENT_NAME}}
        sync = AsyncMock()
        upsert = AsyncMock()
        with patch("app.registry.load_yaml_doc", return_value=doc), \
                patch("app.registry.sync_to_gateway", new=sync), \
                patch("app.registry.upsert_registry_row", new=upsert), \
                patch("app.registry.pool", new=AsyncMock()) as db_pool:
            conn = AsyncMock()
            conn.fetchval.return_value = registry.sha256_doc(doc)
            acquire = type("Acquire", (), {
                "__aenter__": AsyncMock(return_value=conn), "__aexit__": AsyncMock(),
            })()
            db_pool.return_value.acquire = lambda: acquire
            await registry.ensure_system_agent()
        sync.assert_awaited_once_with(registry.SYSTEM_AGENT_NAME, doc)
        upsert.assert_not_awaited()

    async def test_system_agent_repairs_missing_registry_row(self):
        doc = {"metadata": {"name": registry.SYSTEM_AGENT_NAME}}
        upsert = AsyncMock()
        with patch("app.registry.load_yaml_doc", return_value=doc), \
                patch("app.registry.sync_to_gateway", new=AsyncMock()), \
                patch("app.registry.upsert_registry_row", new=upsert), \
                patch("app.registry.pool", new=AsyncMock()) as db_pool:
            conn = AsyncMock()
            conn.fetchval.return_value = None
            acquire = type("Acquire", (), {
                "__aenter__": AsyncMock(return_value=conn), "__aexit__": AsyncMock(),
            })()
            db_pool.return_value.acquire = lambda: acquire
            await registry.ensure_system_agent()
        upsert.assert_awaited_once_with(registry.SYSTEM_AGENT_NAME, doc)


if __name__ == "__main__":
    unittest.main()
