"""DeepSeek thin Runner：OpenAI 兼容、能力降级与垂直切片。"""

import os
import unittest
from unittest.mock import AsyncMock, patch

from app import main, scheduler
from app.runtime import RunRequest, RuntimeServiceError, get_runtime
from app.runtime.deepseek_thin import DeepSeekThinAdapter
from app.runtime.resolver import reset_runtime_for_tests


class FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeOpenAIClient:
    def __init__(self):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, path, json, headers):
        self.calls.append((path, json, headers))
        return FakeResponse({
            "choices": [{"message": {"content": "薄 Runner 交付"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })


def agent_doc():
    return {
        "apiVersion": "agentfleet/v1",
        "spec": {
            "format": "deerflow.custom-agent", "version": 1,
            "agent": {
                "name": "worker-01", "display_name": "Worker",
                "soul": "你是测试 worker", "model": "deepseek-default",
                "model_settings": {"temperature": 0.2},
            },
        },
    }


class DeepSeekThinContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret", "DEEPSEEK_THIN_MODEL": "deepseek-flash"}, clear=True)
        self.env.start()
        self.client = FakeOpenAIClient()
        self.adapter = DeepSeekThinAdapter()
        await self.adapter.deploy_agent("worker-01", agent_doc())

    async def asyncTearDown(self):
        self.env.stop()

    def factory(self, **_kwargs):
        return self.client

    async def test_register_execute_observe_and_requeue(self):
        request = RunRequest(
            agent_name="worker-01", thread_id="thread-1", loop_id=7,
            input_text="原始任务", model_profile="deepseek-default",
        )
        result = await self.adapter.execute_run(request, client_factory=self.factory)
        self.assertEqual((result.text, result.tokens), ("薄 Runner 交付", 15))
        path, body, headers = self.client.calls[0]
        self.assertEqual(path, "/chat/completions")
        self.assertEqual(body["model"], "deepseek-flash")
        self.assertEqual(body["temperature"], 0.2)
        self.assertEqual(body["messages"][-1], {"role": "user", "content": "原始任务"})
        self.assertEqual(headers["Authorization"], "Bearer test-secret")
        self.assertNotIn("test-secret", repr(result))

        overview = await self.adapter.get_overview()
        self.assertEqual((overview["threads"], overview["runs"], overview["tokens_total"]), (1, 1, 15))
        self.assertEqual((await self.adapter.get_thread_state("thread-1"))["checkpoint_id"], None)
        self.assertEqual(len(await self.adapter.get_messages("thread-1")), 2)
        run = await self.adapter.get_run("thread-1", result.run_id)
        self.assertEqual(run["status"], "success")
        self.assertEqual(len(await self.adapter.get_run_messages(result.run_id, {"limit": 50})), 2)
        self.assertEqual((await self.adapter.get_run_events("thread-1", result.run_id))[0]["type"], "run_completed")

        # requeue 保留消息历史，不伪装成 checkpoint resume。
        await self.adapter.execute_run(RunRequest(
            agent_name="worker-01", thread_id="thread-1", loop_id=7,
            input_text="修正后重新执行", model_profile="deepseek-default",
        ), client_factory=self.factory)
        second_body = self.client.calls[1][1]
        self.assertEqual(len(second_body["messages"]), 4)
        self.assertEqual(second_body["messages"][-1]["content"], "修正后重新执行")
        self.assertEqual(len(await self.adapter.get_messages("thread-1")), 4)

    async def test_resume_and_unsupported_capabilities_fail_explicitly(self):
        with self.assertRaises(RuntimeServiceError) as raised:
            await self.adapter.resume_run(RunRequest(
                agent_name="worker-01", thread_id="thread-1", loop_id=7,
                input_text="原始任务", recovery_action="resume", checkpoint_ref="old-state",
            ), client_factory=self.factory)
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(raised.exception.kind, "capability")
        with self.assertRaises(RuntimeServiceError):
            await self.adapter.list_legacy_crons()

    async def test_missing_key_fails_before_model_call(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeServiceError) as raised:
                await self.adapter.execute_run(RunRequest(
                    agent_name="worker-01", thread_id="thread-1", loop_id=7,
                    input_text="任务",
                ), client_factory=self.factory)
        self.assertEqual(raised.exception.status, 503)
        self.assertEqual(self.client.calls, [])

    async def test_legacy_cron_compatibility_is_noop(self):
        with patch("app.main.get_runtime", return_value=self.adapter), \
                patch("app.scheduler.get_runtime", return_value=self.adapter), \
                patch("app.scheduler._retired_gateway_crons", False):
            self.assertEqual(await main._cron_tasks_of("worker-01"), [])
            self.assertEqual(await scheduler._retire_gateway_crons(), 0)


class DispatcherConnection:
    def __init__(self, resume_comment=None):
        self.loop = {
            "id": 7, "task_id": 9, "claimant_type": "agent", "claimant": "worker-01",
            "status": "claimed", "thread_id": "thread-1", "model_profile": "deepseek-default",
            "recovery_action": None, "last_checkpoint_id": None, "resume_comment": resume_comment,
        }
        self.execute = AsyncMock()

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def fetchrow(self, query, *_args):
        if "FROM fleet_task_loops WHERE id=$1 FOR UPDATE" in query:
            return self.loop
        if "FROM fleet_tasks WHERE id=$1" in query:
            return {"id": 9, "input": "原始任务", "agent_name": "worker-01", "parent_task_id": None}
        raise AssertionError(query)


class DispatcherAcquire:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return None


class ThinDispatcherSliceTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_delivery_and_return_requeue(self):
        from app import dispatcher

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret", "DEEPSEEK_THIN_MODEL": "deepseek-flash"}, clear=True):
            adapter = DeepSeekThinAdapter()
            await adapter.deploy_agent("worker-01", agent_doc())
            client = FakeOpenAIClient()
            delivery = AsyncMock()
            for resume_comment in (None, "请补充风险数据"):
                conn = DispatcherConnection(resume_comment)
                with patch("app.dispatcher.pool", AsyncMock(return_value=DispatcherAcquire(conn))), \
                        patch("app.dispatcher._chain", AsyncMock(return_value=[(9, "worker-01")])), \
                        patch("app.dispatcher.get_runtime", return_value=adapter), \
                        patch("app.dispatcher.httpx.AsyncClient", return_value=client), \
                        patch("app.pool.deliver_loop", delivery), \
                        patch("app.dispatcher.audit", AsyncMock()):
                    await dispatcher._run_loop(7)
        self.assertEqual(delivery.await_count, 2)
        self.assertEqual(delivery.await_args_list[0].kwargs["tokens"], 15)
        self.assertIn("薄 Runner 交付", delivery.await_args_list[0].args[1])
        self.assertIn("请补充风险数据", client.calls[1][1]["messages"][-1]["content"])


class ThinResolverTests(unittest.TestCase):
    def test_resolver_selects_thin_runtime(self):
        with patch.dict(os.environ, {"FLEET_RUNTIME": "deepseek-thin"}, clear=True):
            reset_runtime_for_tests()
            self.assertIsInstance(get_runtime(), DeepSeekThinAdapter)
        reset_runtime_for_tests()


if __name__ == "__main__":
    unittest.main()
