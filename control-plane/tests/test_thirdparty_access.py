"""第三方接入回归：单 PAT、执行语义、批量读取、取消传播与能力发现。"""

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.execution import enrich_execution
from app.fap import meta
from app.fap_middleware import pat_auth_middleware, scope_allows
from app.main import app, list_tasks, task_close
from app.runtime import RuntimeServiceError


def acquire_for(conn):
    return type("Acquire", (), {
        "__aenter__": AsyncMock(return_value=conn),
        "__aexit__": AsyncMock(return_value=False),
    })()


def runtime_mock(*, cancel=True, runs=None, cancel_result=(204, None)):
    return SimpleNamespace(
        name="deerflow" if cancel else "deepseek-thin",
        capabilities=lambda: SimpleNamespace(cancel=cancel),
        get_thread=AsyncMock(return_value={"runs": runs or []}),
        cancel_run=AsyncMock(return_value=cancel_result),
    )


class PatScopeTests(unittest.IsolatedAsyncioTestCase):
    def test_write_implies_read_but_read_does_not_imply_write(self):
        self.assertTrue(scope_allows("read", {"write"}))
        self.assertTrue(scope_allows("write", {"write"}))
        self.assertTrue(scope_allows("read", {"read"}))
        self.assertFalse(scope_allows("write", {"read"}))

    async def test_one_write_pat_passes_read_middleware(self):
        request = SimpleNamespace(
            url=SimpleNamespace(path="/api/tasks"),
            method="GET",
            headers={"Authorization": "Bearer dfp_thirdparty"},
            state=SimpleNamespace(),
        )
        downstream = AsyncMock(return_value=JSONResponse({"ok": True}))
        with patch("app.fap_middleware._lookup_pat", new=AsyncMock(return_value={
            "user_id": "u1", "name": "runner", "scopes": {"write"},
        })), patch("app.fap_pats.touch_last_used", new=AsyncMock()) as touch:
            response = await pat_auth_middleware(request, downstream)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.state.fap_actor, "pat:u1:runner")
        downstream.assert_awaited_once_with(request)
        touch.assert_awaited_once_with("dfp_thirdparty")


class ExecutionSemanticsTests(unittest.TestCase):
    def test_permanent_and_exhausted_errors_are_terminal_failures(self):
        for kind in ("permanent", "transient", "recursion"):
            with self.subTest(kind=kind):
                item = enrich_execution({
                    "status": "claimed", "error": "execution stopped",
                    "failure_kind": kind, "recovery_action": None,
                })
                self.assertEqual(item["execution_status"], "failed")
                self.assertTrue(item["is_terminal"])
                self.assertFalse(item["retryable"])
                self.assertEqual(item["last_error"], "execution stopped")
                self.assertEqual(item["worker_state"], "terminal")

    def test_scheduled_retry_and_normal_queue_are_distinct(self):
        retry = enrich_execution({
            "status": "claimed", "error": "provider unavailable",
            "failure_kind": "transient", "recovery_action": "retry",
            "next_retry_at": "2026-11-02T03:00:00Z",
        })
        self.assertEqual(retry["execution_status"], "queued")
        self.assertFalse(retry["is_terminal"])
        self.assertTrue(retry["retryable"])
        self.assertEqual(retry["worker_state"], "retry_scheduled")

        queued = enrich_execution({"status": "claimed", "error": None})
        self.assertEqual(queued["execution_status"], "queued")
        self.assertFalse(queued["is_terminal"])
        self.assertFalse(queued["retryable"])
        self.assertEqual(queued["worker_state"], "queued")

        running = enrich_execution({"status": "running", "error": None})
        self.assertEqual(running["execution_status"], "running")
        self.assertEqual(running["worker_state"], "running")

    def test_latest_loop_error_wins_over_legacy_task_error(self):
        item = enrich_execution({
            "status": "queued", "latest_loop_status": "claimed",
            "error": "old task error", "loop_error": "latest loop error",
        })
        self.assertEqual(item["last_error"], "latest loop error")
        self.assertEqual(item["execution_status"], "failed")


class BatchTaskTests(unittest.IsolatedAsyncioTestCase):
    async def test_ids_are_sql_parameters_and_share_limit_offset_positions(self):
        conn = AsyncMock()
        conn.fetch.return_value = []
        with patch("app.main.pool", new=AsyncMock()) as db_pool:
            db_pool.return_value.acquire = lambda: acquire_for(conn)
            result = await list_tasks(ids="24,25,29", limit=100, offset=0)

        self.assertEqual(result, [])
        sql, *args = conn.fetch.await_args.args
        self.assertIn("t.id=ANY($1::bigint[])", sql)
        self.assertIn("LIMIT $2 OFFSET $3", sql)
        self.assertIn("l.error AS loop_error", sql)
        self.assertEqual(args[0], [24, 25, 29])
        self.assertEqual(args[1], 100)
        self.assertEqual(args[2], 0)

    async def test_ids_rejects_empty_and_more_than_100_entries(self):
        for ids in (",", ",".join(str(i) for i in range(1, 102)), "1,not-a-number"):
            with self.subTest(ids=ids[:60]):
                with self.assertRaises(HTTPException) as raised:
                    await list_tasks(ids=ids)
                self.assertEqual(raised.exception.status_code, 422)


class CloseCancellationTests(unittest.IsolatedAsyncioTestCase):
    def database(self, conn):
        async def db_pool():
            return SimpleNamespace(acquire=lambda: acquire_for(conn))
        return patch("app.main.pool", new=db_pool)

    async def test_close_waits_for_confirmed_cancel_before_archiving(self):
        events = []
        conn = AsyncMock()
        conn.fetch.return_value = [{"id": 21, "thread_id": "thread-1"}]
        conn.fetchval.side_effect = lambda *_args: events.append("archive") or 17
        runtime = runtime_mock(runs=[
            {"run_id": "run-active", "status": "running"},
            {"run_id": "run-done", "status": "success"},
        ])
        runtime.cancel_run.side_effect = lambda *_args, **_kwargs: events.append("cancel") or (204, None)
        with self.database(conn), patch("app.main.get_runtime", return_value=runtime), \
                patch("app.main.audit", new=AsyncMock()) as audit:
            result = await task_close(17, author="third-party")

        self.assertEqual(result, {"pool_status": "done", "cancelled_runs": 1})
        self.assertEqual(events, ["cancel", "archive"])
        runtime.cancel_run.assert_awaited_once_with("thread-1", "run-active", wait=True)
        audit.assert_awaited_once_with(
            "task.closed", actor="third-party",
            detail={"task_id": 17, "cancelled_runs": 1})
        updates = [call.args[0] for call in conn.execute.await_args_list]
        self.assertTrue(any("cancel_requested_at=now()" in sql for sql in updates))
        self.assertTrue(any("SET cancelled_at=now()" in sql for sql in updates))

    async def test_queued_loop_without_runtime_thread_can_close(self):
        conn = AsyncMock()
        conn.fetch.return_value = [{"id": 21, "thread_id": "not-created-yet"}]
        conn.fetchval.return_value = 17
        runtime = runtime_mock()
        runtime.get_thread.side_effect = RuntimeServiceError(404, "thread not found")
        with self.database(conn), patch("app.main.get_runtime", return_value=runtime), \
                patch("app.main.audit", new=AsyncMock()):
            result = await task_close(17)

        self.assertEqual(result, {"pool_status": "done", "cancelled_runs": 0})
        runtime.cancel_run.assert_not_awaited()
        conn.fetchval.assert_awaited_once()

    async def test_cancel_failure_does_not_archive_task(self):
        conn = AsyncMock()
        conn.fetch.return_value = [{"id": 21, "thread_id": "thread-1"}]
        runtime = runtime_mock(runs=[{"run_id": "run-active", "status": "running"}])
        runtime.cancel_run.side_effect = RuntimeServiceError(503, "gateway unavailable")
        with self.database(conn), patch("app.main.get_runtime", return_value=runtime), \
                patch("app.main.audit", new=AsyncMock()) as audit:
            with self.assertRaises(HTTPException) as raised:
                await task_close(17)

        self.assertEqual(raised.exception.status_code, 503)
        conn.fetchval.assert_not_awaited()
        audit.assert_not_awaited()

    async def test_unconfirmed_cancel_does_not_archive_or_mark_cancelled(self):
        conn = AsyncMock()
        conn.fetch.return_value = [{"id": 21, "thread_id": "thread-1"}]
        runtime = runtime_mock(
            runs=[{"run_id": "run-active", "status": "pending"}],
            cancel_result=(202, {"status": "cancel_requested"}),
        )
        with self.database(conn), patch("app.main.get_runtime", return_value=runtime):
            with self.assertRaises(HTTPException) as raised:
                await task_close(17)

        self.assertEqual(raised.exception.status_code, 503)
        conn.fetchval.assert_not_awaited()
        updates = [call.args[0] for call in conn.execute.await_args_list]
        self.assertFalse(any("SET cancelled_at=now()" in sql for sql in updates))

    async def test_runtime_without_cancel_fails_explicitly(self):
        conn = AsyncMock()
        conn.fetch.return_value = [{"id": 21, "thread_id": "thread-1"}]
        runtime = runtime_mock(
            cancel=False, runs=[{"run_id": "run-active", "status": "created"}])
        with self.database(conn), patch("app.main.get_runtime", return_value=runtime):
            with self.assertRaises(HTTPException) as raised:
                await task_close(17)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("不支持取消", raised.exception.detail)
        runtime.cancel_run.assert_not_awaited()
        conn.fetchval.assert_not_awaited()


class MetaDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_meta_reflects_deployment_scopes_routes_and_real_capabilities(self):
        runtime = runtime_mock(cancel=False)
        request = SimpleNamespace(app=app)
        with patch("app.fap.get_runtime", return_value=runtime), \
                patch.dict(os.environ, {"FLEET_DEPLOY_VERSION": "deploy-test-38"}):
            response = await meta(request)
        payload = json.loads(response.body)

        self.assertEqual(payload["api_version"], app.version)
        self.assertEqual(payload["deployment_version"], "deploy-test-38")
        self.assertIn("batch_tasks", payload["capabilities"])
        self.assertIn("failure_semantics", payload["capabilities"])
        self.assertIn("platform_evaluation", payload["capabilities"])
        self.assertNotIn("runtime_cancel", payload["capabilities"])
        self.assertTrue(payload["authentication"]["write_implies_read"])
        self.assertIn("GET", payload["authentication"]["scopes"]["write"])
        self.assertIn("POST", payload["authentication"]["scopes"]["write"])
        self.assertNotIn("POST", payload["authentication"]["scopes"]["read"])
        self.assertEqual(payload["rate_limit"]["retry_after_header"], "Retry-After")

        operations = {item["id"]: item for item in payload["public_operations"]}
        self.assertEqual(operations["tasks.list"]["required_scope"], "read")
        self.assertEqual(operations["tasks.close"]["required_scope"], "write")
        self.assertIn("tasks.conversation", operations)
        self.assertIn("threads.state", operations)
        self.assertNotIn("console.health", operations)
        self.assertNotIn("runs.stream", operations)
        self.assertNotIn("api_key", json.dumps(payload))

    async def test_runtime_cancel_capability_is_advertised_only_when_supported(self):
        with patch("app.fap.get_runtime", return_value=runtime_mock(cancel=True)):
            response = await meta(SimpleNamespace(app=app))
        self.assertIn("runtime_cancel", json.loads(response.body)["capabilities"])


if __name__ == "__main__":
    unittest.main()
