"""Fleet Operations Protocol 覆盖性、默认 Agent 与执行器护栏（ADR-0017）。"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import fleet_tools  # noqa: E402
from app.fap import meta  # noqa: E402
from app.fleet_ops import operations_protocol  # noqa: E402
from app.main import app  # noqa: E402


def load_protocol():
    with (ROOT / "fleet-ops-protocol.json").open(encoding="utf-8") as f:
        return json.load(f)


def caller(name="fleet-ops-01"):
    return {"configurable": {"agent_name": name}}


class FakeResponse:
    status_code = 200
    text = '{"operation_id":"tasks.get","status":200,"result":{"ok":true},"truncated":false}'

    def json(self):
        return {"operation_id": "tasks.get", "status": 200,
                "result": {"ok": True}, "truncated": False}


class FakeClient:
    def __init__(self):
        self.kwargs = None
        self.request_args = None
        self.request_kwargs = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, *args, **kwargs):
        self.request_args = args
        self.request_kwargs = kwargs
        return FakeResponse()


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    def test_operation_ids_are_unique_and_definitions_are_valid(self):
        protocol = load_protocol()
        operations = protocol["operations"]
        ids = [op["id"] for op in operations]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(protocol["surfaces"]), {"control-plane", "console", "gateway"})
        for op in operations:
            with self.subTest(operation=op["id"]):
                self.assertIn(op["surface"], protocol["surfaces"])
                self.assertIn(op["method"], {"GET", "POST", "PUT", "DELETE", "PATCH"})
                self.assertTrue(op["path"].startswith("/"))
                self.assertTrue(op["description"])
                self.assertIn(op.get("confirmation", "standard"), {"standard", "human_only"})

    def test_every_control_plane_api_route_is_registered(self):
        actual = {
            (method, route.path)
            for route in app.routes
            for method in getattr(route, "methods", set())
            if route.path.startswith("/api/")
        }
        registered = {
            (op["method"], op["path"])
            for op in load_protocol()["operations"]
            if op["surface"] == "control-plane"
        }
        self.assertSetEqual(actual - registered, set())

    async def test_protocol_endpoint_and_meta_discovery(self):
        protocol = await operations_protocol()
        self.assertEqual(protocol["protocol"], "am-fleet-ops")
        response = await meta(type("Request", (), {"app": app})())
        payload = json.loads(response.body)
        self.assertEqual(payload["operations_protocol"], "/api/ops/protocol")
        self.assertIn("fleet_operations", payload["capabilities"])

    def test_default_agent_is_least_privilege_and_not_agent_callable(self):
        with (ROOT / "control-plane/agents/fleet-ops-01.yaml").open(encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        agent = doc["spec"]["agent"]
        self.assertEqual(doc["metadata"]["name"], "fleet-ops-01")
        self.assertEqual(agent["tool_groups"], ["fleet:ops"])
        self.assertEqual(agent["mcp_plugins"], [])
        self.assertIsNone(agent["skills"])
        self.assertEqual(agent["allowed_subagents"], [])
        self.assertFalse(agent["memory_enabled"])
        self.assertFalse(doc["triggers"]["agentCallable"])
        self.assertEqual(doc["egress"], [])

    def test_existing_task_assignment_is_discoverable_and_deterministic(self):
        operation = next(op for op in load_protocol()["operations"] if op["id"] == "loops.claim")
        self.assertEqual(operation["method"], "POST")
        self.assertEqual(operation["path"], "/api/tasks/{task_id}/loops")
        self.assertTrue(operation["write"])
        with (ROOT / "control-plane/agents/fleet-ops-01.yaml").open(encoding="utf-8") as f:
            soul = yaml.safe_load(f)["spec"]["agent"]["soul"]
        self.assertIn("改派或指派既有 Task 时，固定使用 loops.claim", soul)
        self.assertIn("不得用 tasks.create 或 agents.dispatch 替代既有任务改派", soul)

    def test_batch_accept_is_single_protocol_call_without_agent_pagination(self):
        operation = next(op for op in load_protocol()["operations"] if op["id"] == "tasks.accept_reviewed")
        self.assertEqual(operation["path"], "/api/tasks/accept-reviewed")
        self.assertTrue(operation["write"])
        with (ROOT / "control-plane/agents/fleet-ops-01.yaml").open(encoding="utf-8") as f:
            soul = yaml.safe_load(f)["spec"]["agent"]["soul"]
        self.assertIn("只调用一次 tasks.accept_reviewed", soul)
        self.assertIn("不再额外查询或分页", soul)


class FleetOpsExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_name_is_not_an_authorization_check(self):
        client = FakeClient()
        with patch("fleet_tools.httpx.AsyncClient", return_value=client):
            result = await fleet_tools._fleet_ops("tasks.list", config=caller("fleet-cmd-01"))
        self.assertEqual(json.loads(result)["status"], 200)
        self.assertEqual(client.request_args, ("/internal/ops/execute",))

    async def test_unknown_operation_is_rejected(self):
        result = await fleet_tools._fleet_ops(" arbitrary.request", config=caller())
        self.assertIn("未登记", result)

    async def test_write_requires_explicit_confirmation(self):
        result = await fleet_tools._fleet_ops(
            "tasks.create", body={"input": "test"}, confirmed=False, config=caller())
        self.assertIn("明确确认", result)

    async def test_human_only_operation_cannot_be_confirmed_away(self):
        result = await fleet_tools._fleet_ops("pats.issue", confirmed=True, config=caller())
        self.assertIn("人工完成", result)

    async def test_non_empty_secrets_never_enter_the_request(self):
        result = await fleet_tools._fleet_ops(
            "mcp.create", body={"name": "private", "secrets": {"TOKEN": "do-not-send"}},
            confirmed=True, config=caller())
        self.assertIn("不得进入 Agent Thread", result)
        self.assertNotIn("do-not-send", result)

    async def test_runner_only_sends_operation_to_credential_proxy(self):
        client = FakeClient()
        with patch("fleet_tools.httpx.AsyncClient", return_value=client) as client_type:
            raw = await fleet_tools._fleet_ops(
                "tasks.get", path_params={"task_id": 12}, config=caller())
        result = json.loads(raw)
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["result"], {"ok": True})
        self.assertEqual(client_type.call_args.kwargs["base_url"], fleet_tools.CP)
        self.assertFalse(client_type.call_args.kwargs["trust_env"])
        self.assertEqual(client.request_args, ("/internal/ops/execute",))
        payload = client.request_kwargs["json"]
        self.assertEqual(payload["operation_id"], "tasks.get")
        self.assertEqual(payload["path_params"], {"task_id": 12})
        self.assertNotIn("Authorization", client.request_kwargs.get("headers", {}))

    async def test_confirmed_write_forwards_body_and_idempotency_key(self):
        client = FakeClient()
        with patch("fleet_tools.httpx.AsyncClient", return_value=client):
            await fleet_tools._fleet_ops(
                "tasks.create", body={"input": "test"}, confirmed=True,
                idempotency_key="ops-test-1", config=caller())
        self.assertEqual(client.request_args, ("/internal/ops/execute",))
        payload = client.request_kwargs["json"]
        self.assertEqual(payload["body"], {"input": "test"})
        self.assertEqual(payload["idempotency_key"], "ops-test-1")
        self.assertTrue(payload["confirmed"])


class DeploymentConfigTests(unittest.TestCase):
    def test_gateway_config_merge_is_idempotent_and_preserves_other_tools(self):
        import tempfile
        from deploy.prepare_gateway_config import prepare

        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            source = Path(tmp) / "source.yaml"
            target = Path(tmp) / "target.yaml"
            source.write_text(yaml.safe_dump({
                "tool_groups": [{"name": "web"}],
                "tools": [
                    {"name": "other", "group": "web", "use": "example:tool"},
                    {"name": "agent_spawn", "group": "wrong", "use": "wrong:tool"},
                ],
            }), encoding="utf-8")
            prepare(source, target)
            prepare(source, target)
            config = yaml.safe_load(target.read_text(encoding="utf-8"))

        groups = [item["name"] for item in config["tool_groups"]]
        self.assertEqual(groups.count("fleet"), 1)
        self.assertEqual(groups.count("fleet:ops"), 1)
        tools = {item["name"]: item for item in config["tools"]}
        self.assertEqual(tools["other"]["use"], "example:tool")
        self.assertEqual(tools["agent_spawn"]["group"], "fleet")
        self.assertEqual(tools["fleet_ops_request"]["use"], "fleet_tools:fleet_ops_request")

    def test_compose_mounts_protocol_and_configures_internal_services(self):
        with (ROOT / "deploy/docker-compose.yml").open(encoding="utf-8") as f:
            compose = yaml.safe_load(f)
        gateway = compose["services"]["gateway"]
        self.assertEqual(gateway["environment"]["FLEET_CONTROL_PLANE"], "http://control-plane:8003")
        self.assertEqual(gateway["environment"]["FLEET_CONSOLE_URL"], "http://console-backend:8002")
        self.assertIn("../fleet-ops-protocol.json:/app/fleet-ops-protocol.json:ro", gateway["volumes"])
        self.assertIn("../deploy/prepare_gateway_config.py:/app/prepare_gateway_config.py:ro", gateway["volumes"])

    def test_control_plane_internal_http_clients_bypass_ambient_proxy(self):
        import ast

        for path in (ROOT / "control-plane/app").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (isinstance(func, ast.Attribute) and func.attr == "AsyncClient"):
                    continue
                if not (isinstance(func.value, ast.Name) and func.value.id == "httpx"):
                    continue
                with self.subTest(file=path.name, line=node.lineno):
                    trust_env = next((kw.value for kw in node.keywords if kw.arg == "trust_env"), None)
                    self.assertIsInstance(trust_env, ast.Constant)
                    self.assertIs(trust_env.value, False)


if __name__ == "__main__":
    unittest.main()
