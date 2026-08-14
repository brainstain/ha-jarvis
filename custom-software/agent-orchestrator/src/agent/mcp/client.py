"""MCP client hub — one connection manager for every MCP server we talk to.

Two transports are supported, per `custom-software/mcp-servers/SPEC.md`:

* ``stdio`` — the orchestrator spawns the server as a subprocess and speaks MCP
  over its stdin/stdout. Used for the custom FastMCP servers and the
  npx-distributed reference servers.
* ``sse``  — the server runs standalone over HTTP and we attach to its SSE
  endpoint. Used for ha-mcp, which lives inside Home Assistant.

Connections are established lazily and cached: the first call that needs a
server pays the handshake cost, everything after it reuses the session. A
per-server :class:`asyncio.Lock` makes that safe under concurrent requests —
two simultaneous chats that both want ``ha-mcp`` produce one subprocess, not
two.

``call_tool`` deliberately does not raise. It returns a structured envelope so
that graph nodes can feed circuit-breaker accounting from a plain dict instead
of wrapping every tool invocation in try/except.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import structlog

log = structlog.get_logger(__name__)

Transport = Literal["stdio", "sse"]

DEFAULT_TIMEOUT = 30.0
HEALTH_TIMEOUT = 5.0


class MCPConfigError(ValueError):
    """A server definition (or the file holding it) is malformed."""


# ──────────────────────────────────────────────────────────────────────
# Server configuration
# ──────────────────────────────────────────────────────────────────────


@dataclass
class MCPServerConfig:
    """One entry from ``config/mcp_servers.json``."""

    name: str
    transport: Transport
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    categories: list[str] = field(default_factory=list)
    enabled: bool = True
    timeout: float = DEFAULT_TIMEOUT

    @classmethod
    def from_dict(cls, data: Any) -> MCPServerConfig:
        """Validate and coerce a raw JSON object into a config.

        Accepts both the key names used in the MCP servers spec (``type``,
        singular ``category``) and the ones used by this module (``transport``,
        plural ``categories``), so a config copied straight out of SPEC.md
        loads without editing.
        """
        if not isinstance(data, dict):
            raise MCPConfigError(f"server entry must be an object, got {type(data).__name__}")

        name = data.get("name")
        if not name or not isinstance(name, str):
            raise MCPConfigError("server entry is missing a string 'name'")

        transport = data.get("transport") or data.get("type")
        if transport not in ("stdio", "sse"):
            raise MCPConfigError(
                f"server '{name}': transport must be 'stdio' or 'sse', got {transport!r}"
            )

        command = data.get("command")
        url = data.get("url")
        if transport == "stdio" and not command:
            raise MCPConfigError(f"server '{name}': stdio transport requires 'command'")
        if transport == "sse" and not url:
            raise MCPConfigError(f"server '{name}': sse transport requires 'url'")

        categories = data.get("categories")
        if categories is None and data.get("category"):
            categories = [data["category"]]
        if categories is None:
            categories = []
        if not isinstance(categories, list) or not all(isinstance(c, str) for c in categories):
            raise MCPConfigError(f"server '{name}': 'categories' must be a list of strings")

        args = data.get("args", [])
        if not isinstance(args, list):
            raise MCPConfigError(f"server '{name}': 'args' must be a list")

        return cls(
            name=name,
            transport=transport,
            command=command,
            args=[str(a) for a in args],
            env={str(k): str(v) for k, v in (data.get("env") or {}).items()},
            cwd=data.get("cwd"),
            url=url,
            headers={str(k): str(v) for k, v in (data.get("headers") or {}).items()},
            categories=categories,
            # `enabled` is ours; `always_available` is the spec's older spelling.
            enabled=bool(data.get("enabled", data.get("always_available", True))),
            timeout=float(data.get("timeout", DEFAULT_TIMEOUT)),
        )


def load_server_configs(path: str | Path) -> list[MCPServerConfig]:
    """Read ``mcp_servers.json`` and return every **enabled** server.

    Raises :class:`MCPConfigError` if the file is missing, is not valid JSON,
    or contains a malformed entry. Callers that must survive a bad config
    (the app lifespan) catch it and continue with no servers.
    """
    file_path = Path(path)
    try:
        raw = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MCPConfigError(f"cannot read MCP config at {file_path}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MCPConfigError(f"invalid JSON in {file_path}: {exc}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("servers"), list):
        raise MCPConfigError(f"{file_path}: expected an object with a 'servers' array")

    configs = [MCPServerConfig.from_dict(entry) for entry in data["servers"]]

    names = [c.name for c in configs]
    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        raise MCPConfigError(f"{file_path}: duplicate server names: {sorted(duplicates)}")

    enabled = [c for c in configs if c.enabled]
    log.info(
        "mcp_config_loaded",
        path=str(file_path),
        total=len(configs),
        enabled=[c.name for c in enabled],
    )
    return enabled


# ──────────────────────────────────────────────────────────────────────
# Result flattening
# ──────────────────────────────────────────────────────────────────────


def _attr(obj: Any, *names: str, default: Any = None) -> Any:
    """First present attribute among `names`.

    The MCP SDK renamed its wire-format fields from camelCase (1.x) to
    snake_case (2.x); pyproject pins only ``mcp>=1.0``, so read both.
    """
    for name in names:
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return default


def flatten_result(result: Any) -> Any:
    """Reduce a ``CallToolResult`` to something an LLM can read.

    * ``structuredContent`` wins when the server provides it.
    * A single text block collapses to its string.
    * Several text blocks join with newlines.
    * Anything non-text (images, embedded resources) is kept as a plain dict.
    """
    structured = _attr(result, "structured_content", "structuredContent")
    if structured is not None:
        return structured

    content = _attr(result, "content", default=[]) or []
    parts: list[Any] = []
    for block in content:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
        elif hasattr(block, "model_dump"):
            parts.append(block.model_dump(mode="json", exclude_none=True))
        else:
            parts.append(block)

    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if all(isinstance(p, str) for p in parts):
        return "\n".join(parts)
    return parts


# ──────────────────────────────────────────────────────────────────────
# The hub
# ──────────────────────────────────────────────────────────────────────


class MCPClientHub:
    """Owns every MCP session and executes tool calls against them."""

    def __init__(self, servers: list[MCPServerConfig] | None = None) -> None:
        self.servers: dict[str, MCPServerConfig] = {s.name: s for s in (servers or [])}
        self._sessions: dict[str, Any] = {}
        self._stacks: dict[str, AsyncExitStack] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # ── lifecycle ────────────────────────────────────────────
    def _lock(self, name: str) -> asyncio.Lock:
        lock = self._locks.get(name)
        if lock is None:
            lock = self._locks[name] = asyncio.Lock()
        return lock

    async def connect(self, name: str) -> Any:
        """Return a live session for `name`, opening one if needed.

        Double-checked under a per-server lock so concurrent requests share a
        single subprocess / SSE stream rather than racing to spawn their own.
        """
        session = self._sessions.get(name)
        if session is not None:
            return session

        config = self.servers.get(name)
        if config is None:
            raise MCPConfigError(f"unknown MCP server: {name}")

        async with self._lock(name):
            session = self._sessions.get(name)
            if session is not None:
                return session

            stack = AsyncExitStack()
            try:
                session = await self._open(config, stack)
            except BaseException:
                await stack.aclose()
                raise

            self._stacks[name] = stack
            self._sessions[name] = session
            log.info("mcp_connected", server=name, transport=config.transport)
            return session

    async def _open(self, config: MCPServerConfig, stack: AsyncExitStack) -> Any:
        """Open the transport and complete the MCP initialize handshake."""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        if config.transport == "stdio":
            params = StdioServerParameters(
                command=config.command or "",
                args=config.args,
                env=config.env or None,
                cwd=config.cwd,
            )
            read, write = await stack.enter_async_context(stdio_client(params))
        else:
            from mcp.client.sse import sse_client

            read, write = await stack.enter_async_context(
                sse_client(
                    url=config.url or "",
                    headers=config.headers or None,
                    timeout=config.timeout,
                )
            )

        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        return session

    async def disconnect(self, name: str) -> None:
        """Close one server's session, tolerating a messy teardown."""
        self._sessions.pop(name, None)
        stack = self._stacks.pop(name, None)
        if stack is None:
            return
        try:
            await stack.aclose()
        except Exception as exc:  # noqa: BLE001 - shutdown must not propagate
            # anyio raises if a transport's cancel scope is exited from a
            # different task than opened it. The process is going away either
            # way, so log and move on rather than failing shutdown.
            log.warning("mcp_disconnect_failed", server=name, error=str(exc))

    async def close(self) -> None:
        """Close every open session."""
        for name in list(self._stacks):
            await self.disconnect(name)

    # ── discovery + execution ────────────────────────────────
    async def list_tools(self, name: str) -> list[dict[str, Any]]:
        """Tools advertised by one server, as plain dicts.

        Raises on connection failure — discovery is the one place a caller
        genuinely wants to know a server is down, so it can skip it.
        """
        session = await self.connect(name)
        response = await session.list_tools()
        tools: list[dict[str, Any]] = []
        for tool in getattr(response, "tools", []) or []:
            tools.append(
                {
                    "name": tool.name,
                    "description": _attr(tool, "description", default="") or "",
                    "input_schema": _attr(tool, "input_schema", "inputSchema", default={}) or {},
                }
            )
        return tools

    async def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Execute a tool and return an envelope — never raise.

        Success::  ``{"tool": ..., "server": ..., "result": <flattened>}``
        Failure::  ``{"tool": ..., "server": ..., "error": "<message>"}``

        A server that reports ``isError`` is a failure too: the distinction
        between "the transport broke" and "the tool said no" doesn't matter to
        circuit-breaker accounting, and collapsing it here keeps graph nodes
        free of try/except.
        """
        envelope: dict[str, Any] = {"tool": tool, "server": server}
        config = self.servers.get(server)
        limit = timeout or (config.timeout if config else DEFAULT_TIMEOUT)

        try:
            session = await self.connect(server)
            result = await asyncio.wait_for(session.call_tool(tool, arguments or {}), timeout=limit)
        except TimeoutError:
            log.warning("mcp_tool_timeout", server=server, tool=tool, timeout=limit)
            return {**envelope, "error": f"timeout after {limit}s"}
        except Exception as exc:  # noqa: BLE001 - surfaced in the envelope
            log.warning("mcp_tool_failed", server=server, tool=tool, error=str(exc))
            return {**envelope, "error": f"{type(exc).__name__}: {exc}"}

        flattened = flatten_result(result)
        if bool(_attr(result, "is_error", "isError", default=False)):
            log.warning("mcp_tool_error_result", server=server, tool=tool)
            return {**envelope, "error": str(flattened)}

        return {**envelope, "result": flattened}

    async def health(self) -> dict[str, str]:
        """Per-server status, probed with a ``tools/list`` ping (spec §All)."""

        async def probe(name: str) -> str:
            try:
                await asyncio.wait_for(self.list_tools(name), timeout=HEALTH_TIMEOUT)
            except TimeoutError:
                return "timeout"
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                return f"unreachable: {type(exc).__name__}"
            return "ok"

        names = list(self.servers)
        if not names:
            return {}
        statuses = await asyncio.gather(*(probe(n) for n in names))
        return dict(zip(names, statuses, strict=True))
