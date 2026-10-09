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

import os

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
