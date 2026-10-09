"""MCP Server 注册管理：业务系统接入的控制面（P3 第一批）。

- fleet_mcp_servers 表 = 注册表（命令/参数/非敏感 env 入库）；
- 敏感值（密码/token 等）只进 secrets 文件（control-plane/secrets/mcp/<name>.json，
  0600，gitignore），API 只写不读，列表只回显键名；
- 渲染 = 注册表 → DeerFlow extensions_config.json（保留非本表管理的 server），
  随后通知网关 cache reset。部署到 ECS 时 secrets 目录挂 KMS 加密盘/Vault 代替。
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from .db import GATEWAY, audit, pool

SECRETS_DIR = os.environ.get(
    "FLEET_MCP_SECRETS_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "secrets", "mcp"),
)
# DeerFlow 项目根（extensions_config.json 所在处）
DEERFLOW_ROOT = os.environ.get(
    "FLEET_DEERFLOW_ROOT",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "spike", "deer-flow"),
)
EXTENSIONS_CONFIG = os.path.join(DEERFLOW_ROOT, "extensions_config.json")

# 平台内置的通用 http 工具（agent 自主对接任意数据网站的能力载体，
# 出口白名单 = agent YAML egress 声明并集）。内置 = 不依赖注册表、不可删除/覆盖，
# 注册表 tab 只面向未来的真实业务 MCP server。
_WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BUILTIN_HTTP = {
    "name": "http",
    "type": "stdio",
    "command": os.path.join(DEERFLOW_ROOT, "backend", ".venv", "bin", "python"),
    "args": [os.path.join(_WORKSPACE_ROOT, "mcp-servers", "http", "server.py")],
    "env": {},
    "enabled": True,
    "description": "平台内置通用 http 工具：agent 自主对接数据网站，出口由 egress 白名单强制。",
}


class McpError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message


def _secret_path(name: str) -> str:
    safe = "".join(c for c in name if c.isalnum() or c in "-_")
    return os.path.join(SECRETS_DIR, f"{safe}.json")


def _write_secrets(name: str, values: dict[str, str]) -> None:
    if not values:
        return
    os.makedirs(SECRETS_DIR, exist_ok=True)
    path = _secret_path(name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(values, f, ensure_ascii=False)
    os.chmod(path, 0o600)


def _read_secrets(name: str) -> dict[str, str]:
    path = _secret_path(name)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _delete_secrets(name: str) -> None:
    path = _secret_path(name)
    if os.path.exists(path):
        os.remove(path)


async def list_servers() -> list[dict[str, Any]]:
    async with (await pool()).acquire() as conn:
        rows = await conn.fetch("SELECT * FROM fleet_mcp_servers ORDER BY name")
    out = []
    for r in rows:
        d = dict(r)
        d["env"] = json.loads(d["env"])
        d["secret_keys"] = json.loads(d["secret_keys"])  # 只有键名，永远没有值
        d["args"] = json.loads(d["args"])
        out.append(d)
    return out


async def upsert_server(
    name: str,
    *,
    command: str,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    secrets: dict[str, str] | None = None,
    description: str = "",
    enabled: bool = True,
    type_: str = "stdio",
    actor: str = "admin",
) -> dict[str, Any]:
    if not name or not command:
        raise McpError(422, "name and command required")
    if name == "http":
        raise McpError(409, "http 是平台内置工具，不经注册表管理；接入业务系统请在 agent YAML 声明 egress")
    async with (await pool()).acquire() as conn:
        old = await conn.fetchrow("SELECT secret_keys FROM fleet_mcp_servers WHERE name=$1", name)
        old_keys = set(json.loads(old["secret_keys"])) if old else set()
    new_keys = set((secrets or {}).keys())
    secret_keys = sorted((old_keys - new_keys) | new_keys)  # 更新即覆盖：旧键未在新值中出现则删除
    if secrets is not None:
        _write_secrets(name, secrets)
        removed = old_keys - new_keys
        if removed:
            cur = _read_secrets(name)
            for k in removed:
                cur.pop(k, None)
            _write_secrets(name, cur)
    async with (await pool()).acquire() as conn:
        await conn.execute(
            """INSERT INTO fleet_mcp_servers
               (name, type, command, args, env, secret_keys, description, enabled, updated_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,now())
               ON CONFLICT (name) DO UPDATE SET
                 type=EXCLUDED.type, command=EXCLUDED.command, args=EXCLUDED.args,
                 env=EXCLUDED.env, secret_keys=EXCLUDED.secret_keys,
                 description=EXCLUDED.description, enabled=EXCLUDED.enabled, updated_at=now()""",
            name, type_, command, json.dumps(args or []), json.dumps(env or {}),
            json.dumps(secret_keys), description, enabled,
        )
    await audit("mcp.registered", actor=actor, args={"name": name, "command": command},
                detail={"secret_keys": secret_keys, "enabled": enabled})
    return {"name": name, "secret_keys": secret_keys}


async def delete_server(name: str, actor: str = "admin") -> None:
    if name == "http":
        raise McpError(409, "http 是平台内置工具，不能删除")
    async with (await pool()).acquire() as conn:
        await conn.execute("DELETE FROM fleet_mcp_servers WHERE name=$1", name)
    _delete_secrets(name)
    await audit("mcp.removed", actor=actor, args={"name": name})


def _render(rows: list[dict[str, Any]], egress_union: list[str] | None = None) -> dict[str, int]:
    if os.path.exists(EXTENSIONS_CONFIG):
        with open(EXTENSIONS_CONFIG, encoding="utf-8") as f:
            doc = json.load(f)
    else:
        doc = {}
    existing: dict[str, Any] = doc.get("mcpServers") or {}
    rows = list(rows)
    managed_names = {r["name"] for r in rows}
    # 只保留"非本注册表管理"的 server：带 fleet_managed 标记的由本注册表全权负责
    # （含已从注册表删除的——否则删除后会残留为"外国 server"）。
    preserved = {k: v for k, v in existing.items()
                 if k not in managed_names and not (isinstance(v, dict) and v.get("fleet_managed"))}

    servers: dict[str, Any] = dict(preserved)
    rows = [r for r in rows if r["name"] != "http"]  # 内置 http 恒在，注册表不得覆盖
    rows.append(dict(BUILTIN_HTTP))
    for r in rows:
        if not r["enabled"]:
            continue
        env = dict(r["env"])
        env.update(_read_secrets(r["name"]))
        if r["name"] == "http":
            # 通用 http 工具的出口白名单 = 所有 agent YAML egress 声明的并集。
            # 接入是 agent 能力（声明在 agent YAML），平台只在这里强制（红线 2）。
            env["ALLOWED_DOMAINS"] = ",".join(sorted(egress_union or []))
        servers[r["name"]] = {
            "enabled": True,
            "type": r["type"],
            "command": r["command"],
            "args": r["args"],
            "env": env,
            "capability": {"id": r["name"]},
            "fleet_managed": True,
            "tool_name_prefix": True,
            "session_init_timeout": 60,
            "tool_call_timeout": 60,
            "description": r["description"],
        }
    doc["mcpServers"] = servers
    doc.setdefault("middlewares", [])
    tmp = EXTENSIONS_CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    os.replace(tmp, EXTENSIONS_CONFIG)
    return {"managed": len(servers) - len(preserved), "preserved": len(preserved)}


async def _egress_union() -> list[str]:
    async with (await pool()).acquire() as conn:
        rows = await conn.fetch("SELECT egress FROM fleet_agents")
    out: set[str] = set()
    for r in rows:
        for d in json.loads(r["egress"] or "[]"):
            out.add(str(d).strip().lower())
    return sorted(out)


async def render_and_reload(actor: str = "system") -> dict[str, Any]:
    rows = await list_servers()
    stats = _render(rows, egress_union=await _egress_union())
    async with httpx.AsyncClient(base_url=GATEWAY, timeout=30) as client:
        r = await client.post("/api/mcp/cache/reset")
        reset = r.status_code == 200
    await audit("mcp.rendered", actor=actor, detail={**stats, "cache_reset": reset,
                "egress_union": await _egress_union()})
    return {**stats, "cache_reset": reset}
