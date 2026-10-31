"""人类 GUI 共享协议执行边界（ADR-0031）。"""

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

import operations


class OperationAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_operation_is_rejected_before_any_request(self):
        with self.assertRaises(HTTPException) as raised:
            await operations.execute_gui_operation(None, operations.GuiOperationBody(operation_id="invalid.operation"), protocol={"operations": []})
        self.assertEqual(raised.exception.status_code, 404)

    async def test_write_requires_explicit_confirmation(self):
        protocol = {"operations": [{"id": "tasks.close", "method": "POST", "path": "/api/tasks/{task_id}/close", "surface": "control-plane", "write": True}]}
        with self.assertRaises(HTTPException) as raised:
            await operations.execute_gui_operation(None, operations.GuiOperationBody(operation_id="tasks.close"), protocol=protocol)
        self.assertEqual(raised.exception.status_code, 409)

    async def test_control_plane_uses_existing_trusted_client_and_real_actor(self):
        protocol = {"operations": [{"id": "tasks.comment", "method": "POST", "path": "/api/tasks/{task_id}/comments", "surface": "control-plane", "write": True, "body": {"author": "fleet-ops-01", "content": "x"}}]}
        request = type("Request", (), {"state": type("State", (), {"dao_user_id": 42})(), "headers": {}})()
        body = operations.GuiOperationBody(operation_id="tasks.comment", path_params={"task_id": 11}, body={"author": "spoofed", "content": "hello"}, confirmed=True, idempotency_key="gui-1")
        with patch("operations._cp", new=AsyncMock(return_value={"status": "ok"})) as cp:
            result = await operations.execute_gui_operation(request, body, protocol=protocol)
        self.assertEqual(result["result"], {"status": "ok"})
        args = cp.await_args.args
        self.assertEqual(args[0], "/tasks/11/comments")
        self.assertEqual(args[1], "POST")
        self.assertEqual(args[2]["author"], "human:42")
        self.assertEqual(cp.await_args.kwargs["idempotency_key"], "gui-1")

    async def test_query_author_cannot_be_spoofed(self):
        protocol = {"operations": [{"id": "tasks.close", "method": "POST", "path": "/api/tasks/{task_id}/close", "surface": "control-plane", "write": True, "query": {"author": "fleet-ops-01"}}]}
        request = type("Request", (), {"state": type("State", (), {"dao_user_id": 42})(), "headers": {}})()
        body = operations.GuiOperationBody(operation_id="tasks.close", path_params={"task_id": 11}, query={"author": "spoofed"}, confirmed=True)
        with patch("operations._cp", new=AsyncMock(return_value={"pool_status": "done"})) as cp:
            await operations.execute_gui_operation(request, body, protocol=protocol)
        self.assertIn("author=human%3A42", cp.await_args.args[0])

    async def test_browser_never_uses_runner_internal_executor(self):
        self.assertNotIn("/internal/ops/execute", operations.GUI_EXECUTE_PATH)


if __name__ == "__main__":
    unittest.main()
