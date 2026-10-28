"""Fleet 协作工具（runner 侧）：agent_spawn / agent_message。

注册方式（不改 DeerFlow 源码）：config.yaml tools: 段声明
  - name: agent_spawn
    group: fleet
    use: fleet_tools:agent_spawn
  - name: agent_message
    group: fleet
    use: fleet_tools:agent_message
网关 PYTHONPATH 需含本文件所在目录；control-plane 地址经 FLEET_CONTROL_PLANE 注入。

- agent_spawn：指挥官把子任务投入任务池（ADR-0010）。tool_groups 含 fleet 的 agent 可用。
- agent_message：异步唤醒目标 agent（进其 Inbox，攒批合并后入池领取）。
当前 agent 名从 LangGraph RunnableConfig 的 configurable.agent_name 解析（DeerFlow 运行时已注入）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

CP = os.environ.get("FLEET_CONTROL_PLANE", "http://127.0.0.1:8003")


def _caller(config: RunnableConfig | None) -> str:
    cfg = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    return cfg.get("agent_name") or "unknown-agent"


async def _cp(path: str, body: dict) -> str:
    async with httpx.AsyncClient(base_url=CP, timeout=30) as client:
        r = await client.post(path, json=body)
        if r.status_code >= 400:
            return f"失败：{r.status_code} {r.text[:200]}"
        return r.text[:500]


class SpawnArgs(BaseModel):
    task: str = Field(description="交给子任务执行者的完整任务说明（含交付要求）")
    assignee: str | None = Field(default=None, description="指定领取的 agent 名；不填 = 开放抢单（≤3 loop）")
    acceptance_hint: str = Field(default="", description="给验收人的备注（可选）")


async def _spawn(task: str, assignee: str | None = None, acceptance_hint: str = "",
                 config: RunnableConfig = None) -> str:
    caller = _caller(config)
    cfg = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    body = {
        "input": task + (f"\n\n【验收备注】{acceptance_hint}" if acceptance_hint else ""),
        "source": "agent",
        "assignee": assignee,
        "created_by": caller,
        "caller_thread_id": cfg.get("thread_id"),
    }
    out = await _cp("/api/tasks", body)
    return f"子任务已入池（created_by={caller}）：{out}"


class MessageArgs(BaseModel):
    agent: str = Field(description="目标 agent 名")
    content: str = Field(description="消息内容；进对方 Inbox，攒批后唤醒对方入池领取")


async def _message(agent: str, content: str, config: RunnableConfig = None) -> str:
    caller = _caller(config)
    out = await _cp(f"/api/agents/{agent}/inbox",
                    {"payload": f"[来自 {caller}] {content}", "source": "agent"})
    return f"消息已投递到 {agent} 的 Inbox：{out}"


agent_spawn = StructuredTool.from_function(
    coroutine=_spawn,
    name="agent_spawn",
    description=(
        "把子任务投入舰队任务池：其它 agent（或人）可领取执行，交付后入 review 由你验收。"
        "用于并行拆分工作。任务说明必须自包含（执行者看不到你的上下文）。"
    ),
    args_schema=SpawnArgs,
)

agent_message = StructuredTool.from_function(
    coroutine=_message,
    name="agent_message",
    description="异步消息唤醒目标 agent（不等结果）：消息进对方 Inbox，对方攒批后入池领取执行。",
    args_schema=MessageArgs,
)


# ---------------------------------------------------------------- Fleet 全量操作协议（ADR-0017）

OPS_PROTOCOL_PATH = Path(__file__).resolve().with_name("fleet-ops-protocol.json")


def _ops_operation(operation_id: str) -> dict:
    with OPS_PROTOCOL_PATH.open(encoding="utf-8") as f:
        protocol = json.load(f)
    for operation in protocol["operations"]:
        if operation["id"] == operation_id:
            return operation
    raise ValueError(f"未登记的 Fleet 操作：{operation_id}；请先读取 system.protocol")


class FleetOpsArgs(BaseModel):
    operation_id: str = Field(description="system.protocol 中登记的操作 ID，例如 tasks.list 或 agents.update")
    path_params: dict = Field(default_factory=dict, description="路径变量，例如 {'task_id': 123}")
    query: dict = Field(default_factory=dict, description="可选查询参数")
    body: dict | None = Field(default=None, description="可选 JSON 请求体")
    confirmed: bool = Field(default=False, description="写操作必须获得用户明确确认后才可为 true")
    idempotency_key: str | None = Field(default=None, description="写操作建议填写的唯一幂等键")


async def _fleet_ops(operation_id: str, path_params: dict | None = None,
                     query: dict | None = None, body: dict | None = None,
                     confirmed: bool = False, idempotency_key: str | None = None,
                     config: RunnableConfig = None) -> str:
    # Agent 名称不是授权依据；系统 PAT 仅由控制面执行代理持有和注入（ADR-0018）。
    try:
        operation = _ops_operation(operation_id)
    except (OSError, ValueError, KeyError) as exc:
        return f"失败：{exc}"
    if operation.get("confirmation") == "human_only":
        return f"该操作涉及秘密或文件下载，请在页面人工完成：{operation['description']}"
    if operation.get("write") and not confirmed:
        return "拒绝：这是写操作。请先向用户展示动作与关键参数并获得明确确认，再以 confirmed=true 调用。"
    for field in operation.get("secret_fields") or []:
        if body and body.get(field):
            return f"拒绝：字段 {field} 含秘密，必须在页面人工配置，不得进入 Agent Thread。"
    payload = {
        "operation_id": operation_id,
        "path_params": path_params or {},
        "query": query or {},
        "body": body,
        "confirmed": confirmed,
        "idempotency_key": idempotency_key,
    }
    try:
        timeout = min(max(float(operation.get("timeout_seconds", 60)), 5), 600) + 5
        async with httpx.AsyncClient(base_url=CP, timeout=timeout, trust_env=False) as client:
            response = await client.post("/internal/ops/execute", json=payload)
        text = response.text
        try:
            result = response.json() if text else None
        except ValueError:
            result = text[:30000]
        if response.status_code >= 400:
            return json.dumps({
                "operation_id": operation_id,
                "status": response.status_code,
                "result": result,
            }, ensure_ascii=False, default=str)
        return json.dumps(result, ensure_ascii=False, default=str)
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        return f"失败：{type(exc).__name__}: {exc}"


fleet_ops_request = StructuredTool.from_function(
    coroutine=_fleet_ops,
    name="fleet_ops_request",
    description=(
        "执行 Fleet Operations Protocol 中的操作。先以 operation_id=system.protocol 读取完整清单；"
        "查询可直接执行，写操作必须先获得用户明确确认并设置 confirmed=true。"
        "不得用于清单外 URL；PAT 签发、非空 secrets 和二进制下载由人工完成。"
    ),
    args_schema=FleetOpsArgs,
)
