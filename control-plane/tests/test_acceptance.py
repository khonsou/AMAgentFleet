"""ADR-0015：人与 Agent 同权验收及权威身份审计（不依赖 PG）。"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.main import (
    AcceptBody, AcceptReviewedBody, DeliverBody, ReturnBody, _acceptance_author,
    loop_deliver, loop_return, task_accept, tasks_accept_reviewed,
)
from app.pool import PoolError


def request(pat_actor=None):
    state = SimpleNamespace()
    if pat_actor is not None:
        state.fap_actor = pat_actor
    return SimpleNamespace(state=state)


class AcceptanceIdentityTests(unittest.TestCase):
    def test_pat_identity_overrides_self_reported_author(self):
        self.assertEqual(
            _acceptance_author(request("pat:user-7:third-party"), "spoofed-admin"),
            "pat:user-7:third-party",
        )

    def test_internal_agent_and_console_identity_stay_compatible(self):
        self.assertEqual(_acceptance_author(request(), "fleet-reviewer"), "fleet-reviewer")
        self.assertEqual(_acceptance_author(request(), "human"), "human")


class AcceptanceEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_any_pat_agent_can_accept_with_audited_identity(self):
        accept = AsyncMock()
        with patch("app.main.poolmod.accept_loop", accept):
            result = await task_accept(
                11, AcceptBody(loop_id=21, author="self-reported"), request("pat:u1:external"))
        self.assertEqual(result, {"pool_status": "done"})
        accept.assert_awaited_once_with(11, 21, author="pat:u1:external")

    async def test_any_pat_agent_can_return_with_audited_identity(self):
        send_back = AsyncMock()
        with patch("app.main.poolmod.return_loop", send_back):
            result = await loop_return(
                21, ReturnBody(comment="请补充数据来源", author="self-reported"),
                request("pat:u1:external"))
        self.assertEqual(result, {"status": "returned"})
        send_back.assert_awaited_once_with(21, "请补充数据来源", author="pat:u1:external")

    async def test_batch_accept_is_one_call_with_authoritative_audited_identity(self):
        payload = {
            "accepted": [{"task_id": 11, "loop_id": 21}],
            "skipped": [], "accepted_count": 1, "remaining_review": 0,
        }
        accept_all = AsyncMock(return_value=payload)
        with patch("app.main.poolmod.accept_pending_review", accept_all):
            result = await tasks_accept_reviewed(
                AcceptReviewedBody(author="self-reported"), request("pat:u1:external"))
        self.assertEqual(result, payload)
        accept_all.assert_awaited_once_with(author="pat:u1:external")

    async def test_invalid_delivery_returns_conflict(self):
        deliver = AsyncMock(side_effect=PoolError(409, "loop 已归档"))
        with patch("app.main.poolmod.human_deliver", deliver):
            with self.assertRaises(HTTPException) as caught:
                await loop_deliver(21, DeliverBody(content="迟到交付", author="human"))

        self.assertEqual(caught.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
