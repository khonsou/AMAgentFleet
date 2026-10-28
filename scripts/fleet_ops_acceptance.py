#!/usr/bin/env python3
"""Fleet Operations Protocol 67 项在线验收（ADR-0017）。

只使用协议 operation ID 驱动请求；测试资源带唯一前缀，凭证仅保存在进程内存。
退出码非零即存在未通过操作。报告只记录状态与断言，不保存响应正文或秘密。
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
CP = os.environ.get("FLEET_CONTROL_PLANE", "http://127.0.0.1:8003")
CONSOLE = os.environ.get("FLEET_CONSOLE_URL", "http://127.0.0.1:8002")
GATEWAY = os.environ.get("FLEET_GATEWAY_URL", "http://127.0.0.1:8001")
BASES = {"control-plane": CP, "console": CONSOLE, "gateway": GATEWAY}
ACTOR = "fleet-ops-acceptance"


class Acceptance:
    def __init__(self, report_path: Path):
        self.protocol = json.loads((ROOT / "fleet-ops-protocol.json").read_text(encoding="utf-8"))
        self.operations = {op["id"]: op for op in self.protocol["operations"]}
        assert len(self.operations) == 67, "协议操作数应为 67"
        self.report_path = report_path
        self.results: list[dict[str, Any]] = []
        self.seen: set[str] = set()
        self.run_id = uuid.uuid4().hex[:10]
        self.prefix = f"ops-accept-{self.run_id}"
        self.agent_name = f"ops-agent-{self.run_id}"
        self.mcp_name = f"ops-mcp-{self.run_id}"
        self.task_ids: list[int] = []
        self.thread_ids: list[str] = []
        self.pat_ids: list[str] = []
        self.pat_tokens: list[str] = []
        self.agent_doc: dict[str, Any] | None = None
        self.http = httpx.AsyncClient(timeout=180, trust_env=False)

    async def close(self):
        await self.http.aclose()

    async def raw(self, method: str, url: str, **kwargs) -> httpx.Response:
        response = await self.http.request(method, url, **kwargs)
        return response

    async def call(
        self,
        operation_id: str,
        *,
        path: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        body: Any = None,
        headers: dict[str, str] | None = None,
        expected: tuple[int, ...] = (200, 201, 202, 204),
        check=None,
        timeout: float | None = None,
    ) -> Any:
        assert operation_id not in self.seen, f"重复执行 {operation_id}"
        self.seen.add(operation_id)
        op = self.operations[operation_id]
        rendered = op["path"]
        for key, value in (path or {}).items():
            rendered = rendered.replace("{" + key + "}", quote(str(value), safe="/" if key == "path" else ""))
        assert "{" not in rendered, f"{operation_id} 缺少路径参数: {rendered}"
        try:
            response = await self.http.request(
                op["method"], BASES[op["surface"]] + rendered,
                params=query, json=body, headers=headers,
                timeout=timeout or op.get("timeout_seconds", 180),
            )
            content_type = response.headers.get("content-type", "")
            payload = response.json() if response.content and "json" in content_type else response.text
            assert response.status_code in expected, f"HTTP {response.status_code}: {response.text[:500]}"
            if check:
                check(payload, response)
            self.results.append({"operation": operation_id, "status": "passed", "http": response.status_code})
            print(f"PASS {operation_id} ({response.status_code})", flush=True)
            return payload
        except Exception as exc:
            self.results.append({"operation": operation_id, "status": "failed", "error": str(exc)[:800]})
            print(f"FAIL {operation_id}: {exc}", flush=True)
            raise

    async def run(self):
        await self.system_and_templates()
        await self.agent_lifecycle()
        await self.task_loop_lifecycle()
        await self.scheduler_and_mcp()
        await self.thread_run_lifecycle()
        await self.third_party_pat()
        await self.system_credential()
        await self.cleanup_operations()
        await self.security_scan()

    async def system_and_templates(self):
        await self.call("system.meta", check=lambda p, _: (_ for _ in ()).throw(AssertionError("缺少协议入口")) if p.get("operations_protocol") != "/api/ops/protocol" else None)
        await self.call("system.agent_doc", check=lambda p, r: (_ for _ in ()).throw(AssertionError("文档为空或协议头缺失")) if not p.strip() or r.headers.get("x-protocol-version") != "1.0" else None)
        await self.call("system.protocol", check=lambda p, _: (_ for _ in ()).throw(AssertionError("在线协议不是 67 项")) if len(p.get("operations", [])) != 67 else None)
        await self.call("system.openapi", check=lambda p, _: (_ for _ in ()).throw(AssertionError("OpenAPI 无路径")) if not p.get("paths") else None)
        await self.call("system.health", check=lambda p, _: (_ for _ in ()).throw(AssertionError("控制面不健康")) if p.get("status") != "ok" else None)
        await self.call("console.health", check=lambda p, _: (_ for _ in ()).throw(AssertionError("Console 不健康")) if p.get("console") != "ok" else None)
        await self.call("overview.get", check=lambda p, _: (_ for _ in ()).throw(AssertionError("概览字段缺失")) if "threads" not in p or "tokens_total" not in p else None)
        await self.call("fleet.threads", check=lambda p, _: (_ for _ in ()).throw(AssertionError("fleet 非列表")) if not isinstance(p, list) else None)
        await self.call("audit.list", query={"limit": 10}, check=lambda p, _: (_ for _ in ()).throw(AssertionError("审计非列表")) if not isinstance(p, list) else None)
        templates = await self.call("templates.list", check=lambda p, _: (_ for _ in ()).throw(AssertionError("无 Agent 模板")) if not p else None)
        rendered = await self.call("templates.render", query={"actor": ACTOR}, body={
            "templateId": templates[0]["id"], "name": self.agent_name,
            "displayName": "Ops Acceptance Agent", "owner": "qa", "costCenter": "QA-OPS", "egressDomain": "",
        }, check=lambda p, _: (_ for _ in ()).throw(AssertionError("模板未渲染 YAML")) if p.get("yaml", {}).get("metadata", {}).get("name") != self.agent_name else None)
        self.agent_doc = rendered["yaml"]
        self.agent_doc.setdefault("spec", {}).setdefault("agent", {})["soul"] = "你是自动化验收 Agent。只简洁回答用户，不调用工具，不创建任务。"
        self.agent_doc["spec"]["agent"]["memory_enabled"] = False
        self.agent_doc["triggers"] = {"web": True, "im": False, "agentCallable": False, "cron": []}

    async def agent_lifecycle(self):
        assert self.agent_doc
        await self.call("agents.create", query={"actor": ACTOR}, body={"yaml": self.agent_doc})
        detail = await self.call("agents.get", path={"name": self.agent_name})
        assert detail["registry"]["name"] == self.agent_name
        await self.call("agents.list", check=lambda p, _: (_ for _ in ()).throw(AssertionError("Registry 未出现测试 Agent")) if self.agent_name not in {a["name"] for a in p} else None)
        updated = copy.deepcopy(self.agent_doc)
        updated["metadata"]["displayName"] = "Ops Acceptance Agent Updated"
        await self.call("agents.update", path={"name": self.agent_name}, query={"actor": ACTOR}, body={"yaml": updated})
        self.agent_doc = updated
        stopped = await self.call("agents.stop", path={"name": self.agent_name}, query={"actor": ACTOR})
        assert stopped["status"] == "hibernated"
        started = await self.call("agents.start", path={"name": self.agent_name}, query={"actor": ACTOR})
        assert started["status"] == "registered"
        inbox = await self.call("inbox.post", path={"name": self.agent_name}, body={"payload": "仅记录在线验收消息，无需执行任务。", "source": "agent"})
        assert inbox["inbox_id"] > 0
        messages = await self.call("inbox.list", path={"name": self.agent_name}, query={"limit": 10})
        assert any(m["id"] == inbox["inbox_id"] for m in messages)
        dispatched = await self.call("agents.dispatch", path={"name": self.agent_name}, body={"input": f"[{self.prefix}] 只回复 OK。", "source": "agent", "actor": ACTOR})
        self.task_ids.append(dispatched["task_id"])

    async def task_loop_lifecycle(self):
        created = await self.call("tasks.create", body={"input": f"[{self.prefix}] 人机交付验收测试", "created_by": ACTOR, "source": "agent", "parent_task_id": None})
        task_id = created["task_id"]
        self.task_ids.append(task_id)
        await self.call("tasks.list", query={"limit": 100}, check=lambda p, _: (_ for _ in ()).throw(AssertionError("任务列表无新任务")) if task_id not in {t["id"] for t in p} else None)
        await self.call("tasks.get", path={"task_id": task_id}, check=lambda p, _: (_ for _ in ()).throw(AssertionError("任务初始状态错误")) if p["task"]["pool_status"] != "open" else None)
        await self.call("tasks.comment", path={"task_id": task_id}, body={"author": ACTOR, "content": "在线验收评论", "loop_id": None})
        claimed = await self.call("loops.claim", path={"task_id": task_id}, body={"claimant_type": "human", "claimant": ACTOR, "created_by": ACTOR})
        loop_id = claimed["loop_id"]
        delivered = await self.call("loops.deliver", path={"loop_id": loop_id}, body={"content": "第一版真实交付", "author": ACTOR})
        assert delivered["status"] == "delivered"
        await self.call("tasks.review_queue", check=lambda p, _: (_ for _ in ()).throw(AssertionError("交付未进入待验收队列")) if task_id not in {t["id"] for t in p} else None)
        returned = await self.call("loops.return", path={"loop_id": loop_id}, body={"comment": "请补充第二版", "author": ACTOR})
        assert returned["status"] == "returned"
        again = await self.raw("POST", f"{CP}/api/loops/{loop_id}/deliver", json={"content": "第二版真实交付", "author": ACTOR})
        assert again.status_code == 200, again.text
        accepted = await self.call("tasks.accept", path={"task_id": task_id}, body={"loop_id": loop_id, "author": ACTOR})
        assert accepted["pool_status"] == "done"
        detail = await self.raw("GET", f"{CP}/api/tasks/{task_id}")
        data = detail.json()
        assert data["task"]["accepted_loop_id"] == loop_id and data["loops"][0]["status"] == "accepted"
        assert any(c["content"] == "请补充第二版" for c in data["comments"])

        # 批量验收必须一次调用闭环，不能由 Agent 逐项循环及额外分页统计。
        batch_created = await self.raw("POST", f"{CP}/api/tasks", json={
            "input": f"[{self.prefix}] 批量验收回归", "source": "agent", "created_by": ACTOR})
        assert batch_created.status_code == 201, batch_created.text
        batch_task_id = batch_created.json()["task_id"]
        self.task_ids.append(batch_task_id)
        batch_claim = await self.raw("POST", f"{CP}/api/tasks/{batch_task_id}/loops", json={
            "claimant_type": "human", "claimant": ACTOR, "created_by": ACTOR})
        batch_loop_id = batch_claim.json()["loop_id"]
        batch_delivery = await self.raw("POST", f"{CP}/api/loops/{batch_loop_id}/deliver", json={
            "content": "批量验收真实交付", "author": ACTOR})
        assert batch_delivery.status_code == 200, batch_delivery.text
        batch = await self.call("tasks.accept_reviewed", body={"author": ACTOR})
        assert {"task_id": batch_task_id, "loop_id": batch_loop_id} in batch["accepted"], batch
        assert batch["accepted_count"] == len(batch["accepted"])
        batch_detail = await self.raw("GET", f"{CP}/api/tasks/{batch_task_id}")
        batch_data = batch_detail.json()
        assert batch_data["task"]["pool_status"] == "done"
        assert batch_data["task"]["accepted_loop_id"] == batch_loop_id

        # 等价权限关键路径：通过系统 PAT 执行代理把既有未分配 Task 指派给另一个 Agent，
        # 不是 human claim，也不是新建任务或 agents.dispatch 兼容层。
        agent_task = await self.raw("POST", f"{CP}/api/tasks", json={
            "input": f"[{self.prefix}] PAT Agent 既有任务改派验证，只回复 OK。",
            "source": "agent", "created_by": ACTOR,
        })
        assert agent_task.status_code == 201, agent_task.text
        agent_task_id = agent_task.json()["task_id"]
        self.task_ids.append(agent_task_id)
        assigned = await self.raw("POST", f"{CP}/internal/ops/execute", json={
            "operation_id": "loops.claim", "path_params": {"task_id": agent_task_id},
            "body": {"claimant_type": "agent", "claimant": self.agent_name, "created_by": "fleet-ops-01"},
            "confirmed": True, "idempotency_key": f"{self.prefix}-agent-claim",
        })
        assert assigned.status_code == 200, assigned.text
        assigned_result = assigned.json()
        assert assigned_result["status"] == 201, assigned_result
        assigned_loop_id = assigned_result["result"]["loop_id"]
        assigned_detail = await self.raw("GET", f"{CP}/api/tasks/{agent_task_id}")
        assigned_data = assigned_detail.json()
        assert assigned_data["task"]["agent_name"] == self.agent_name
        assert any(loop["id"] == assigned_loop_id and loop["claimant_type"] == "agent"
                   and loop["claimant"] == self.agent_name for loop in assigned_data["loops"])
        self.results.append({"operation": "loops.claim:agent-via-system-pat", "status": "passed", "http": 201})
        print("PASS loops.claim:agent-via-system-pat (201)", flush=True)

        # 重派面向滞留 returned Loop。真实 return 会立即自动续跑，不能用瞬时状态
        # 构造确定性测试；这里仅准备滞留夹具，重派本身仍走真实 API 与 Dispatcher。
        import asyncpg
        stale_task = await self.raw("POST", f"{CP}/api/tasks", json={"input": f"[{self.prefix}] 滞留 Loop 重派", "source": "agent", "created_by": ACTOR})
        stale_task_id = stale_task.json()["task_id"]
        self.task_ids.append(stale_task_id)
        conn = await asyncpg.connect(os.environ.get("CONSOLE_PG_DSN", "postgresql://postgres@127.0.0.1:5432/deerflow"))
        try:
            requeue_loop = await conn.fetchval(
                "INSERT INTO fleet_task_loops (task_id, claimant_type, claimant, created_by, status) VALUES ($1,'agent',$2,$3,'returned') RETURNING id",
                stale_task_id, self.agent_name, ACTOR)
            await conn.execute("UPDATE fleet_tasks SET agent_name=$2 WHERE id=$1", stale_task_id, self.agent_name)
        finally:
            await conn.close()
        requeued = await self.call("loops.requeue", path={"loop_id": requeue_loop})
        assert requeued["status"] == "claimed"

        dispatch_task = self.task_ids[0]
        by_thread = None
        for _ in range(30):
            detail = (await self.raw("GET", f"{CP}/api/tasks/{dispatch_task}")).json()
            by_thread = detail.get("latest_thread_id") or next((l.get("thread_id") for l in detail["loops"] if l.get("thread_id")), None)
            if by_thread:
                break
            await asyncio.sleep(1)
        assert by_thread, "Agent Loop 未生成 Thread"
        self.thread_ids.append(by_thread)
        context = await self.call("loops.by_thread", path={"thread_id": by_thread})
        assert context["loop"]["task_id"] == dispatch_task
        closed = await self.call("tasks.close", path={"task_id": dispatch_task}, query={"author": ACTOR})
        assert closed["pool_status"] == "done"

    async def wait_for_loop(self, task_id: int) -> dict:
        for _ in range(30):
            response = await self.raw("GET", f"{CP}/api/tasks/{task_id}")
            data = response.json()
            if data.get("loops"):
                return data["loops"][-1]
            await asyncio.sleep(1)
        raise AssertionError(f"task {task_id} 未生成 Loop")

    async def scheduler_and_mcp(self):
        assert self.agent_doc
        cron_doc = copy.deepcopy(self.agent_doc)
        cron_doc["triggers"]["cron"] = [{"cron": "0 0 1 1 *", "prompt": "不会在当前验收窗口到期"}]
        await self.raw("PUT", f"{CP}/api/agents/{self.agent_name}", params={"actor": ACTOR}, json={"yaml": cron_doc})
        synced = await self.call("scheduler.sync")
        assert "synced" in synced or "fired" in synced
        self.agent_doc = cron_doc
        mcp_body = {"name": self.mcp_name, "command": "/bin/cat", "args": [], "env": {}, "secrets": {}, "description": "disabled acceptance fixture", "enabled": False, "actor": ACTOR}
        await self.call("mcp.create", body=mcp_body)
        await self.call("mcp.update", path={"name": self.mcp_name}, body={**mcp_body, "description": "updated disabled acceptance fixture"})
        servers = await self.call("mcp.list")
        item = next(s for s in servers if s["name"] == self.mcp_name)
        assert item["description"].startswith("updated") and item["secret_keys"] == []
        rendered = await self.call("mcp.render", query={"actor": ACTOR})
        assert "managed" in rendered
        await self.call("mcp.destroy", path={"name": self.mcp_name}, query={"actor": ACTOR})

    def run_body(self, thread_id: str, text: str):
        return {"assistant_id": self.agent_name, "input": {"messages": [{"role": "user", "content": text}]}, "config": {"configurable": {"thread_id": thread_id}}}

    async def thread_run_lifecycle(self):
        created = await self.call("threads.create", body={"assistant_id": self.agent_name, "recursion_limit": 100})
        tid = created["thread_id"]
        self.thread_ids.append(tid)
        detail = await self.call("threads.get", path={"tid": tid})
        assert detail["thread"]["thread_id"] == tid
        await self.call("threads.messages", path={"tid": tid}, check=lambda p, _: (_ for _ in ()).throw(AssertionError("消息响应非列表")) if not isinstance(p, list) else None)
        await self.call("threads.list", check=lambda p, _: (_ for _ in ()).throw(AssertionError("Thread 列表无新实例")) if tid not in {t["thread_id"] for t in p} else None)
        context = await self.call("threads.review_context", path={"tid": tid})
        assert context["loop"] is None

        upload = await self.raw("POST", f"{GATEWAY}/api/threads/{tid}/uploads", files={"files": ("acceptance.txt", b"fleet ops artifact acceptance", "text/plain")})
        assert upload.status_code == 200, upload.text
        artifact_url = upload.json()["files"][0]["artifact_url"]
        marker = f"/api/threads/{tid}/artifacts/"
        assert marker in artifact_url, artifact_url
        artifact_path = artifact_url.split(marker, 1)[1]
        await self.call("threads.artifact", path={"tid": tid, "path": artifact_path}, check=lambda p, _: (_ for _ in ()).throw(AssertionError("产物内容不一致")) if p != "fleet ops artifact acceptance" else None)

        waited = await self.call("runs.wait", body=self.run_body(tid, "只回复 WAIT_OK。"), timeout=180, check=lambda p, _: (_ for _ in ()).throw(AssertionError("Run 执行错误")) if p.get("status") == "error" else None)
        assert waited
        detail = (await self.raw("GET", f"{CONSOLE}/api/console/threads/{tid}")).json()
        run_id = detail["runs"][0]["run_id"]
        run = await self.call("runs.get", path={"thread_id": tid, "run_id": run_id})
        assert run["run_id"] == run_id and run["thread_id"] == tid
        await self.call("runs.messages", path={"run_id": run_id}, query={"limit": 50}, check=lambda p, _: (_ for _ in ()).throw(AssertionError("Run messages 无 data")) if "data" not in p else None)
        events = await self.call("runs.events", path={"thread_id": tid, "run_id": run_id})
        assert isinstance(events, (list, dict))
        state = await self.call("threads.state", path={"thread_id": tid})
        assert state.get("values") is not None
        usage = await self.call("threads.token_usage", path={"thread_id": tid})
        assert "total_tokens" in usage or "totals" in usage or "tokens" in usage

        sse = await self.call("runs.stream", body=self.run_body(tid, "只回复 STREAM_OK。"), timeout=180)
        assert "event:" in sse and "data:" in sse
        sent = await self.call("threads.send", path={"tid": tid}, body={"content": "只回复 SEND_OK。", "recursion_limit": 100}, timeout=180)
        assert sent and sent.get("status") != "error"

        feedback = await self.call("runs.feedback_create", path={"thread_id": tid, "run_id": run_id}, body={"rating": 1, "comment": "acceptance", "message_id": None})
        feedback_id = feedback["feedback_id"]
        listed = await self.call("runs.feedback_list", path={"thread_id": tid, "run_id": run_id})
        assert any(f["feedback_id"] == feedback_id for f in listed)
        stats = await self.call("runs.feedback_stats", path={"thread_id": tid, "run_id": run_id})
        assert stats["total"] >= 1 and stats["positive"] >= 1
        deleted = await self.call("runs.feedback_delete_by_id", path={"thread_id": tid, "run_id": run_id, "feedback_id": feedback_id})
        assert deleted["success"] is True
        upserted = await self.call("runs.feedback", path={"thread_id": tid, "run_id": run_id}, body={"rating": 1, "comment": "acceptance upsert"})
        assert upserted["rating"] == 1
        deleted_current = await self.call("runs.feedback_delete", path={"thread_id": tid, "run_id": run_id})
        assert deleted_current["success"] is True

        # 独立流式 Run 启动后立即取消；wait=true 确保运行已终止。
        async with self.http.stream("POST", f"{GATEWAY}/api/runs/stream", json=self.run_body(tid, "请写一篇很长的自动化测试说明。"), timeout=60) as response:
            assert response.status_code == 200, await response.aread()
            location = response.headers.get("content-location", "")
            cancel_run_id = location.rstrip("/").split("/")[-1]
            assert cancel_run_id
            cancelled = await self.call("runs.cancel", path={"thread_id": tid, "run_id": cancel_run_id}, query={"wait": "true"}, expected=(202, 204))
            assert cancelled in ("", None) or cancelled
        await self.call("threads.destroy", path={"tid": tid})
        self.thread_ids.remove(tid)

    async def issue_pat(self, scopes: list[str]) -> dict:
        response = await self.raw("POST", f"{CONSOLE}/api/console/pats", json={"name": f"{self.prefix}-{'-'.join(scopes)}", "scopes": scopes, "expires_in_days": 1})
        assert response.status_code == 201, response.text
        payload = response.json()
        self.pat_ids.append(payload["id"])
        self.pat_tokens.append(payload["token"])
        return payload

    async def third_party_pat(self):
        await self.call("pats.list")
        read_pat = await self.issue_pat(["read"])
        # pats.issue 走 human-only Console 路径，而不是 Agent 可调用的控制面路径。
        response = await self.raw("POST", f"{CONSOLE}/api/console/pats", json={"name": f"{self.prefix}-human", "scopes": ["read"], "expires_in_days": 1})
        assert response.status_code == 201
        human_pat = response.json()
        self.pat_ids.append(human_pat["id"])
        self.pat_tokens.append(human_pat["token"])
        self.results.append({"operation": "pats.issue", "status": "passed", "http": 201, "surface": "console-human"})
        self.seen.add("pats.issue")
        print("PASS pats.issue (201, console-human)", flush=True)
        headers = {"Authorization": f"Bearer {read_pat['token']}"}
        read_response = await self.raw("GET", f"{CONSOLE}/api/health", headers=headers)
        assert read_response.status_code == 200 and read_response.headers.get("x-protocol-version") == "1.0"
        denied = await self.raw("POST", f"{CONSOLE}/api/tasks", headers=headers, json={"input": "read scope 不得写入"})
        assert denied.status_code == 403, denied.text

        write_pat = await self.issue_pat(["write"])
        write_headers = {"Authorization": f"Bearer {write_pat['token']}", "Idempotency-Key": f"idem-{self.run_id}"}
        body = {"input": f"[{self.prefix}] 第三方幂等测试", "source": "agent", "created_by": ACTOR}
        first = await self.raw("POST", f"{CONSOLE}/api/tasks", headers=write_headers, json=body)
        second = await self.raw("POST", f"{CONSOLE}/api/tasks", headers=write_headers, json=body)
        assert first.status_code == 201 and second.status_code == 201
        assert first.json()["task_id"] == second.json()["task_id"] and second.headers.get("x-idempotent-replay") == "true"
        self.task_ids.append(first.json()["task_id"])
        conflict = await self.raw("POST", f"{CONSOLE}/api/tasks", headers=write_headers, json={**body, "input": "不同正文必须冲突"})
        assert conflict.status_code == 409, conflict.text

        limited_pat = await self.issue_pat(["read"])
        limited_headers = {"Authorization": f"Bearer {limited_pat['token']}"}
        statuses = []
        for _ in range(61):
            limited = await self.raw("GET", f"{CONSOLE}/api/health", headers=limited_headers)
            statuses.append(limited.status_code)
        assert 429 in statuses, f"未触发 PAT 限速: {statuses[-5:]}"

        # 吊销验证使用未触发限速的凭证；限速中间件先于鉴权，已限流凭证会继续得到 429。
        revoke = await self.call("pats.revoke", path={"pat_id": read_pat["id"]}, query={"author": ACTOR})
        assert revoke["status"] == "revoked"
        self.pat_ids.remove(read_pat["id"])
        revoked = await self.raw("GET", f"{CONSOLE}/api/health", headers=headers)
        assert revoked.status_code == 401

    async def system_credential(self):
        before = await self.call(
            "ops.credential_status",
            check=lambda p, _: (_ for _ in ()).throw(AssertionError("系统凭证状态不安全"))
            if not p.get("configured") or p.get("scopes") != ["read", "write"] or "token" in p else None,
        )
        denied = await self.raw("DELETE", f"{CP}/api/pats/fap-system-fleet-ops-01")
        assert denied.status_code == 403, denied.text
        response = await self.raw("POST", f"{CONSOLE}/api/console/ops/credential/rotate")
        assert response.status_code == 200, response.text
        rotated = response.json()
        assert rotated["version"] == before["version"] + 1
        assert "token" not in rotated and "ciphertext" not in rotated and "token_digest" not in rotated
        self.results.append({
            "operation": "ops.credential_rotate", "status": "passed",
            "http": response.status_code, "surface": "console-human",
        })
        self.seen.add("ops.credential_rotate")
        print("PASS ops.credential_rotate (200, console-human)", flush=True)

    async def cleanup_operations(self):
        await self.call("agents.destroy", path={"name": self.agent_name}, query={"actor": ACTOR})

    async def security_scan(self):
        # 所有测试 PAT 均不得出现在审计或本测试创建的 Thread 消息中。
        audit = await self.raw("GET", f"{CP}/api/audit", params={"limit": 500})
        assert audit.status_code == 200
        audit_text = audit.text
        for token in self.pat_tokens:
            assert token not in audit_text, "PAT 泄漏到审计"
        for tid in self.thread_ids:
            messages = await self.raw("GET", f"{GATEWAY}/api/threads/{tid}/messages")
            if messages.status_code == 200:
                for token in self.pat_tokens:
                    assert token not in messages.text, "PAT 泄漏到 Thread"
        for pat_id in list(self.pat_ids):
            response = await self.raw("DELETE", f"{CP}/api/pats/{pat_id}", params={"author": ACTOR})
            assert response.status_code == 200, response.text
            self.pat_ids.remove(pat_id)
        for task_id in self.task_ids:
            detail = await self.raw("GET", f"{CP}/api/tasks/{task_id}")
            if detail.status_code == 200 and detail.json()["task"]["pool_status"] != "done":
                await self.raw("POST", f"{CP}/api/tasks/{task_id}/close", params={"author": ACTOR})

    def write_report(self, completed: bool):
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "protocol": self.protocol["protocol"], "version": self.protocol["version"],
            "run_id": self.run_id, "completed": completed,
            "total": len(self.operations), "executed": len(self.seen),
            "passed": sum(r["status"] == "passed" for r in self.results),
            "failed": sum(r["status"] == "failed" for r in self.results),
            "results": self.results,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: report[k] for k in ("total", "executed", "passed", "failed", "completed")}, ensure_ascii=False), flush=True)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=ROOT / "artifacts" / "fleet-ops-acceptance.json")
    args = parser.parse_args()
    acceptance = Acceptance(args.report)
    completed = False
    try:
        await acceptance.run()
        completed = True
    finally:
        # 异常路径也尽力销毁 Agent、MCP 和剩余 PAT；正常路径已由协议操作完成。
        for pat_id in list(acceptance.pat_ids):
            try:
                await acceptance.raw("DELETE", f"{CP}/api/pats/{pat_id}", params={"author": ACTOR})
            except Exception:
                pass
        try:
            await acceptance.raw("DELETE", f"{CP}/api/mcp-servers/{acceptance.mcp_name}", params={"actor": ACTOR})
        except Exception:
            pass
        if "agents.destroy" not in acceptance.seen:
            try:
                await acceptance.raw("DELETE", f"{CP}/api/agents/{acceptance.agent_name}", params={"actor": ACTOR})
            except Exception:
                pass
        for task_id in acceptance.task_ids:
            try:
                detail = await acceptance.raw("GET", f"{CP}/api/tasks/{task_id}")
                if detail.status_code == 200 and detail.json()["task"]["pool_status"] != "done":
                    await acceptance.raw("POST", f"{CP}/api/tasks/{task_id}/close", params={"author": ACTOR})
            except Exception:
                pass
        acceptance.write_report(completed)
        await acceptance.close()
    assert completed, "在线验收未全部通过，详见报告"


if __name__ == "__main__":
    asyncio.run(main())
