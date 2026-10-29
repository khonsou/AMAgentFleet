"""ADR-0012：控制面常驻单例门闩与 dispatcher 懒启动门控。"""

import asyncio
import os
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from app import dispatcher, main
from app.singleton import SingletonGate


class StartupGateTests(unittest.IsolatedAsyncioTestCase):
    async def run_startup(self, acquired):
        scheduler_loop = AsyncMock()
        inbox_loop = AsyncMock()
        with (
            patch("app.main.init_db", new=AsyncMock()),
            patch("app.main.registry.ensure_system_agent", new=AsyncMock()),
            patch("app.main.ops_credentials.ensure_system_credential", new=AsyncMock()),
            patch("app.main.ops_credentials.check_system_credential_readable", new=AsyncMock()),
            patch("app.main.dispatcher.recover_claimed", new=AsyncMock(return_value=0)),
            patch("app.main.singleton.acquire", new=AsyncMock(return_value=acquired)) as acquire,
            patch("app.main.singleton.release", new=AsyncMock()) as release,
            patch("app.main.scheduler.sync_loop", new=scheduler_loop),
            patch("app.main.inbox.flush_loop", new=inbox_loop),
        ):
            await main.startup()
            state = dict(main._state)
            background_enabled = dispatcher._background_enabled
            await main.shutdown()
        acquire.assert_awaited_once_with()
        release.assert_awaited_once_with()
        return state, background_enabled, scheduler_loop, inbox_loop

    async def test_lock_owner_starts_scheduler_and_inbox(self):
        state, enabled, scheduler_loop, inbox_loop = await self.run_startup(True)

        self.assertTrue(enabled)
        self.assertEqual(set(state), {"scheduler", "inbox"})
        scheduler_loop.assert_called_once_with()
        inbox_loop.assert_called_once_with()

    async def test_standby_skips_all_startup_loops_but_still_initializes(self):
        state, enabled, scheduler_loop, inbox_loop = await self.run_startup(False)

        self.assertFalse(enabled)
        self.assertEqual(state, {})
        scheduler_loop.assert_not_called()
        inbox_loop.assert_not_called()


class DispatcherGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await dispatcher.shutdown()
        dispatcher._QUEUE = asyncio.Queue()

    async def asyncTearDown(self):
        await dispatcher.shutdown()

    async def test_standby_enqueue_does_not_start_consumer(self):
        consumer = AsyncMock()
        with (
            patch("app.dispatcher.WORKERS", 1),
            patch("app.dispatcher._consumer", new=consumer),
        ):
            dispatcher.set_background_enabled(False)
            await dispatcher.enqueue_loop_run(101)
            await asyncio.sleep(0)
            self.assertFalse(consumer.called)
            self.assertEqual(dispatcher._QUEUE.qsize(), 1)

            dispatcher.set_background_enabled(True)
            await dispatcher.enqueue_loop_run(102)
            await asyncio.sleep(0)
            self.assertTrue(consumer.called)

    async def test_consumer_loop_is_inert_on_standby(self):
        dispatcher.set_background_enabled(False)
        with patch("app.dispatcher._consumer", new=AsyncMock()) as consumer:
            await asyncio.wait_for(dispatcher.consumer_loop(), timeout=0.1)
        consumer.assert_not_called()


@unittest.skipUnless(os.environ.get("FLEET_TEST_PG_DSN"), "未配置 FLEET_TEST_PG_DSN")
class PostgresSingletonGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_instance_cannot_acquire_until_first_releases(self):
        lock_key = uuid.uuid4().int & ((1 << 63) - 1)
        first = SingletonGate(lock_key=lock_key)
        second = SingletonGate(lock_key=lock_key)
        with patch("app.singleton.PG_DSN", os.environ["FLEET_TEST_PG_DSN"]):
            try:
                self.assertTrue(await first.acquire())
                self.assertTrue(first.acquired)
                self.assertFalse(await second.acquire())
                self.assertFalse(second.acquired)

                await first.release()
                self.assertTrue(await second.acquire())
                self.assertTrue(second.acquired)
            finally:
                await first.release()
                await second.release()


if __name__ == "__main__":
    unittest.main()
