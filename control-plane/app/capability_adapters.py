"""Generic remote-agent and MCP adapters for the Runtime Capability Plane."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
import uuid
from typing import Any
from urllib.parse import urlparse

import httpx

from .capability_plane import CapabilityError


def _config(provider: dict[str, Any]) -> dict[str, Any]:
    return provider["manifest"].get("config") or {}


def _safe_base_url(provider: dict[str, Any]) -> str:
    config = _config(provider)
    base_url = config.get("base_url")
    if not isinstance(base_url, str):
        raise CapabilityError(422, "INVALID_REMOTE_URL", "Provider 需要 config.base_url")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise CapabilityError(422, "INVALID_REMOTE_URL", "仅支持无内嵌凭据的 http/https URL")
    allow_private = config.get("allow_private") is True
    if not allow_private:
        try:
            addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
        except socket.gaierror as exc:
            raise CapabilityError(422, "REMOTE_DNS_FAILED", str(exc)) from exc
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                raise CapabilityError(422, "PRIVATE_REMOTE_FORBIDDEN", "私有／回环地址需要显式 allow_private=true")
    return base_url.rstrip("/")


def _headers(provider: dict[str, Any]) -> dict[str, str]:
    if provider["manifest"].get("auth_handle"):
        raise CapabilityError(
            501,
            "AUTH_REQUIRED",
            "授权句柄需要由 Gateway 注入；当前 Adapter 不读取秘密，也不转发调用方 PAT",
        )
    return {"Content-Type": "application/json", "Accept": "application/json"}


def _http_error(exc: httpx.HTTPError) -> CapabilityError:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        mapping = {
            401: "AUTH_REQUIRED",
            403: "AUTH_REQUIRED",
            404: "PROVIDER_NOT_FOUND",
            409: "CONFIG_STALE",
            429: "PROVIDER_RATE_LIMITED",
        }
        return CapabilityError(status, mapping.get(status, "PROVIDER_FAILED"), f"Remote HTTP {status}")
    return CapabilityError(502, "PROVIDER_UNREACHABLE", str(exc)[:500])


def _json_or_output(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"output": response.text}


class RemoteAgentAdapter:
    async def verify(self, provider: dict[str, Any]) -> dict[str, Any]:
        base_url = _safe_base_url(provider)
        config = _config(provider)
        protocol = config.get("protocol", "fap")
        try:
            async with httpx.AsyncClient(timeout=float(config.get("timeout_seconds", 30)), trust_env=False) as client:
                if protocol == "fap":
                    agent = config.get("agent_name") or provider["provider_id"]
                    response = await client.get(f"{base_url}/api/agents/{agent}", headers=_headers(provider))
                    if response.status_code == 404:
                        return {"status": "unavailable", "verified": False, "verification": "remote_agent_not_found"}
                    response.raise_for_status()
                    document = response.json()
                    registry = document.get("registry") or {}
                    if registry.get("status") not in {"registered", "running"}:
                        return {"status": "unavailable", "verified": False, "verification": "remote_agent_inactive"}
                    return {
                        "status": "verified",
                        "verified": True,
                        "verification": "remote_fap_agent",
                        "remote_config_version": registry.get("config_version"),
                    }
                if protocol == "http":
                    health_path = config.get("health_path") or "/health"
                    response = await client.get(base_url + health_path, headers=_headers(provider))
                    response.raise_for_status()
                    return {"status": "verified", "verified": True, "verification": "remote_http_health"}
                raise CapabilityError(422, "INVALID_REMOTE_PROTOCOL", protocol)
        except httpx.HTTPError as exc:
            raise _http_error(exc) from exc

    async def invoke(self, provider: dict[str, Any], capability: dict[str, Any], input_value: Any) -> tuple[Any, dict[str, Any]]:
        protocol = _config(provider).get("protocol", "fap")
        if protocol == "fap":
            return await self._invoke_fap(provider, capability, input_value)
        if protocol == "http":
            return await self._invoke_http(provider, capability, input_value)
        raise CapabilityError(422, "INVALID_REMOTE_PROTOCOL", protocol)

    async def _invoke_http(self, provider, capability, input_value) -> tuple[Any, dict[str, Any]]:
        base_url = _safe_base_url(provider)
        config = _config(provider)
        request = capability.get("request") or {}
        method = (request.get("method") or "POST").upper()
        path = request.get("path")
        if not isinstance(path, str) or not path.startswith("/") or ".." in path or "://" in path:
            raise CapabilityError(422, "INVALID_REMOTE_PATH", "远程 operation path 必须是站内绝对路径")
        try:
            async with httpx.AsyncClient(timeout=float(config.get("timeout_seconds", 30)), trust_env=False) as client:
                response = await client.request(
                    method,
                    base_url + path,
                    headers=_headers(provider),
                    params=input_value if method in {"GET", "DELETE"} and isinstance(input_value, dict) else None,
                    json=input_value if method not in {"GET", "DELETE"} else None,
                )
                response.raise_for_status()
                return _json_or_output(response), {"transport": "http", "method": method, "path": path}
        except httpx.HTTPError as exc:
            raise _http_error(exc) from exc

    async def _invoke_fap(self, provider, capability, input_value) -> tuple[Any, dict[str, Any]]:
        base_url = _safe_base_url(provider)
        config = _config(provider)
        agent = config.get("agent_name") or provider["provider_id"]
        model_profile = config.get("model_profile") or "deepseek-default"
        prompt = f"""执行远程能力调用。
operation_id={capability['operation_id']}
input={json.dumps(input_value, ensure_ascii=False)}
output_schema={json.dumps(capability['output_schema'], ensure_ascii=False)}
只输出符合 output_schema 的 JSON。"""
        key = f"remote-capability:{uuid.uuid4().hex}"
        body = {
            "input": prompt,
            "source": "agent",
            "actor": "runtime-capability-plane",
            "model_profile": model_profile,
            "subagent_model_policy": "auto",
            "idempotency_key": key,
        }
        try:
            async with httpx.AsyncClient(timeout=float(config.get("timeout_seconds", 30)), trust_env=False) as client:
                created = await client.post(
                    f"{base_url}/api/agents/{agent}/dispatch",
                    headers={**_headers(provider), "Idempotency-Key": key, "X-Client-Request-Id": key},
                    json=body,
                )
                created.raise_for_status()
                task_id = created.json().get("task_id") or created.json().get("id")
                if not task_id:
                    raise CapabilityError(502, "PROVIDER_FAILED", "Remote Agent 未返回 task_id")
                deadline = asyncio.get_running_loop().time() + float(config.get("run_timeout_seconds", 120))
                while True:
                    detail_response = await client.get(f"{base_url}/api/tasks/{task_id}", headers=_headers(provider))
                    detail_response.raise_for_status()
                    detail = detail_response.json()
                    loops = detail.get("loops") or []
                    if loops:
                        loop = max(loops, key=lambda item: int(item.get("id") or 0))
                        status = loop.get("execution_status")
                        if status == "succeeded" and loop.get("deliverable"):
                            raw = loop["deliverable"]
                            try:
                                output = json.loads(raw)
                            except (ValueError, TypeError):
                                output = {"output": raw}
                            return output, {"transport": "fap", "task_id": task_id, "loop_id": loop.get("id")}
                        if status in {"failed", "closed"}:
                            raise CapabilityError(502, "PROVIDER_FAILED", loop.get("last_error") or status, {"task_id": task_id})
                    if asyncio.get_running_loop().time() >= deadline:
                        await client.post(
                            f"{base_url}/api/tasks/{task_id}/close?author=capability-timeout",
                            headers={**_headers(provider), "Idempotency-Key": f"{key}:close"},
                        )
                        raise CapabilityError(504, "PROVIDER_TIMEOUT", "Remote Agent 调用超时", {"task_id": task_id})
                    await asyncio.sleep(1)
        except httpx.HTTPError as exc:
            raise _http_error(exc) from exc


class McpSession:
    def __init__(self, provider: dict[str, Any]):
        self.provider = provider
        self.config = _config(provider)
        self.transport = self.config.get("transport", "http")
        self.process = None
        self.client = None
        self.session_id = None
        self.next_id = 1

    async def __aenter__(self):
        if self.transport == "stdio":
            command = self.config.get("command")
            args = self.config.get("args") or []
            if not isinstance(command, str) or not command or not isinstance(args, list):
                raise CapabilityError(422, "INVALID_MCP_COMMAND", "stdio MCP 需要 command 与 args 数组")
            environment = {"PATH": os.environ.get("PATH", "")}
            for key, value in (self.config.get("env") or {}).items():
                if not isinstance(key, str) or not key.replace("_", "a").isalnum() or not isinstance(value, (str, int, float, bool)):
                    raise CapabilityError(422, "INVALID_MCP_ENV", "MCP env 只支持非敏感标量键值")
                environment[key] = str(value)
            self.process = await asyncio.create_subprocess_exec(
                command,
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=environment,
            )
        elif self.transport == "http":
            self.client = httpx.AsyncClient(
                base_url=_safe_base_url(self.provider),
                timeout=float(self.config.get("timeout_seconds", 30)),
                trust_env=False,
            )
        else:
            raise CapabilityError(422, "INVALID_MCP_TRANSPORT", self.transport)
        await self.request("initialize", {
            "protocolVersion": self.config.get("protocol_version", "2024-11-05"),
            "capabilities": {},
            "clientInfo": {"name": "am-agent-fleet", "version": "1.0"},
        })
        await self.notify("notifications/initialized")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.client is not None:
            await self.client.aclose()
        if self.process is not None:
            if self.process.returncode is None:
                self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=3)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()

    async def notify(self, method: str) -> None:
        await self._send({"jsonrpc": "2.0", "method": method})

    async def request(self, method: str, params: Any = None) -> Any:
        request_id = self.next_id
        self.next_id += 1
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        response = await self._send(payload, request_id=request_id)
        if response.get("error"):
            error = response["error"]
            raise CapabilityError(502, "MCP_ERROR", error.get("message") or str(error))
        return response.get("result")

    async def _send(self, payload: dict[str, Any], request_id: int | None = None) -> Any:
        if self.transport == "stdio":
            return await self._send_stdio(payload, request_id)
        return await self._send_http(payload, request_id)

    async def _send_stdio(self, payload, request_id):
        if self.process is None or self.process.stdin is None or self.process.stdout is None:
            raise CapabilityError(500, "MCP_SESSION_CLOSED", "MCP stdio 未启动")
        self.process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())
        await self.process.stdin.drain()
        if request_id is None:
            return None
        while True:
            line = await asyncio.wait_for(self.process.stdout.readline(), timeout=float(self.config.get("timeout_seconds", 30)))
            if not line:
                stderr = await self.process.stderr.read() if self.process.stderr else b""
                raise CapabilityError(502, "MCP_CLOSED", stderr.decode(errors="replace")[:500] or "MCP stdio closed")
            try:
                response = json.loads(line)
            except ValueError:
                continue
            if response.get("id") == request_id:
                return response

    async def _send_http(self, payload, request_id):
        if self.client is None:
            raise CapabilityError(500, "MCP_SESSION_CLOSED", "MCP HTTP 未启动")
        headers = {
            **_headers(self.provider),
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        try:
            response = await self.client.post("", headers=headers, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise _http_error(exc) from exc
        if self.session_id is None:
            self.session_id = response.headers.get("Mcp-Session-Id")
        if request_id is None:
            return None
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            for line in response.text.splitlines():
                if line.startswith("data:"):
                    try:
                        data = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if data.get("id") == request_id:
                        return data
            raise CapabilityError(502, "MCP_ERROR", "SSE 中未找到对应 JSON-RPC 响应")
        data = response.json()
        if data.get("id") != request_id:
            raise CapabilityError(502, "MCP_ERROR", "JSON-RPC 响应 id 不匹配")
        return data


class McpAdapter:
    async def verify(self, provider: dict[str, Any]) -> dict[str, Any]:
        async with McpSession(provider) as session:
            result = await session.request("tools/list")
        available = {tool.get("name") for tool in (result or {}).get("tools", [])}
        declared = {item["operation_id"] for item in provider["manifest"]["capabilities"]}
        missing = sorted(declared - available)
        if missing:
            return {"status": "unavailable", "verified": False, "verification": "mcp_tools_missing", "missing": missing}
        return {"status": "verified", "verified": True, "verification": "mcp_tools_list", "tool_count": len(available)}

    async def invoke(self, provider: dict[str, Any], capability: dict[str, Any], input_value: Any) -> tuple[Any, dict[str, Any]]:
        async with McpSession(provider) as session:
            result = await session.request("tools/call", {
                "name": capability["operation_id"],
                "arguments": input_value,
            })
        if not isinstance(result, dict):
            return {"output": result}, {"transport": _config(provider).get("transport", "http")}
        if result.get("isError"):
            raise CapabilityError(502, "PROVIDER_FAILED", "MCP tool returned isError=true", result)
        if isinstance(result.get("structuredContent"), dict):
            # structuredContent 也可能采用 {"result": <json>} 信封，需在统一输出前解包。
            result = result["structuredContent"]
        content = result.get("content")
        if isinstance(content, list):
            texts = [item.get("text") for item in content if isinstance(item, dict) and item.get("type") == "text"]
            if len(texts) == 1:
                try:
                    return json.loads(texts[0]), {"transport": _config(provider).get("transport", "http")}
                except (ValueError, TypeError):
                    return {"output": texts[0]}, {"transport": _config(provider).get("transport", "http")}
        if set(result) == {"result"}:
            inner = result["result"]
            if isinstance(inner, dict):
                return inner, {"transport": _config(provider).get("transport", "http")}
            if isinstance(inner, str):
                try:
                    return json.loads(inner), {"transport": _config(provider).get("transport", "http")}
                except (ValueError, TypeError):
                    return {"output": inner}, {"transport": _config(provider).get("transport", "http")}
        return result, {"transport": _config(provider).get("transport", "http")}
