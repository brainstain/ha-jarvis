"""MCP tool registry — discovery, category mapping, and LLM-facing rendering.

This is the bridge between :mod:`agent.mcp.client` (transport) and
:mod:`agent.mcp.tool_filter` (the max-7 selection rule). It walks every
configured server, asks it what tools it has, files each tool under one or more
categories, and can render a selected subset as OpenAI-style function
definitions for LiteLLM.

Discovery is deliberately fault-tolerant: a server that refuses to start (a
missing ``npx`` package, Home Assistant mid-restart) is logged and skipped. One
broken server must never cost us the tools of the ones that work.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

from agent.mcp.client import MCPClientHub, MCPConfigError, MCPServerConfig, load_server_configs
from agent.mcp.tool_filter import CATEGORY_SERVERS, ToolRegistry, ToolSchema

log = structlog.get_logger(__name__)

# Separator between server and tool in a qualified name. Double underscore
# keeps names inside the OpenAI function-name charset ([a-zA-Z0-9_-]), which
# rules out the more obvious dot.
QUALIFIER = "__"

# Servers with no category mapping anywhere still need somewhere to live, or
# their tools would be invisible to the filter.
DEFAULT_CATEGORY = "other"


def qualified_name(server: str, tool: str) -> str:
    """Globally unique tool name — two servers may both expose ``search``."""
    return f"{server}{QUALIFIER}{tool}"


def categories_for_server(server: str) -> list[str]:
    """Categories that :mod:`tool_filter` associates with this server name.

    A server can land in several categories — ``mcp-filesystem`` serves both
    ``documents`` and ``filesystem`` — so this returns a list, in the order the
    categories are declared in ``CATEGORY_SERVERS``.
    """
    return [cat for cat, servers in CATEGORY_SERVERS.items() if server in servers]


def render_openai_tools(tools: list[ToolSchema]) -> list[dict[str, Any]]:
    """Render selected tools as OpenAI-style function definitions for LiteLLM."""
    rendered: list[dict[str, Any]] = []
    for tool in tools:
        parameters = tool.input_schema or {"type": "object", "properties": {}}
        rendered.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or f"{tool.name} (via {tool.server})",
                    "parameters": parameters,
                },
            }
        )
    return rendered


# Parameters the caller already knows and injects server-side (never trusted
# from model output, since they're identity/authorization-relevant). Hiding
# them from the model also removes the single biggest source of
# tool-selection failures under testing: the model endlessly reasoning about
# a value ("user_id") it has no way to know.
_INJECTED_PARAMS = {"user_id"}


def render_tool_selection_schema(tools: list[ToolSchema]) -> dict[str, Any]:
    """JSON schema for Ollama's grammar-constrained decoding: pick a tool
    (enum-constrained) and a loose arguments object.

    Native OpenAI-style function-calling (``tools=[...]``) does not
    reliably produce ``tool_calls`` on this stack — verified directly
    against the live model: even with the LiteLLM plumbing bug fixed
    (ollama_chat/ prefix), real multi-tool schemas still frequently exhaust
    the token budget mid-reasoning with no tool_calls emitted (0/5 in
    testing). This mirrors router.py's `_routing_schema()` fix for the same
    class of problem: grammar-constrained decoding guarantees a
    syntactically valid, on-schema choice at the sampling level.

    Arguments aren't schema-constrained per-tool — different tools' argument
    shapes vary too much for one flat JSON schema to express as a strict
    union, and the model isn't reliable enough yet to fill a conditional
    schema correctly. The model gets each tool's parameters as text in the
    prompt instead (see `render_tool_descriptions`), and arguments are
    parsed the same tolerant way native tool_calls always were.
    """
    names = [t.name for t in tools]
    return {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "enum": [*names, "none"]},
            "arguments": {"type": "object"},
        },
        "required": ["tool", "arguments"],
    }


def render_tool_descriptions(tools: list[ToolSchema]) -> str:
    """Human-readable tool listing for the grammar-constrained selection prompt.

    Omits `_INJECTED_PARAMS` — the caller fills those in after parsing, so
    the model is never asked to supply them.
    """
    lines: list[str] = []
    for tool in tools:
        props = tool.input_schema.get("properties") or {}
        required = set(tool.input_schema.get("required") or [])
        visible = {k: v for k, v in props.items() if k not in _INJECTED_PARAMS}
        params = ", ".join(
            f"{name}{'' if name in required else '?'}: {info.get('type', 'any')}"
            for name, info in visible.items()
        )
        description = tool.description or f"{tool.name} (via {tool.server})"
        lines.append(f"- {tool.name}({params}): {description}")
    return "\n".join(lines)


def parse_tool_selection(
    content: str, tools: list[ToolSchema]
) -> tuple[str | None, dict[str, Any]]:
    """Parse grammar-constrained tool-selection output into (name, args).

    Returns (None, {}) for "none", an unrecognized tool name, or content
    that fails to parse — callers treat all three as "no tool selected"
    rather than raising, same tolerance native tool_calls parsing always had.
    """
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1].removeprefix("json").strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None, {}

    name = data.get("tool")
    valid_names = {t.name for t in tools}
    if name not in valid_names:
        return None, {}

    args = data.get("arguments")
    if not isinstance(args, dict):
        args = {}
    return name, args


def inject_identity_args(
    tool: ToolSchema, args: dict[str, Any], user_id: str
) -> dict[str, Any]:
    """Fill in identity parameters the model was never shown, always
    overriding any value the model tried to supply anyway (it's never
    trustworthy for authorization-relevant fields)."""
    props = tool.input_schema.get("properties") or {}
    if "user_id" in props:
        args["user_id"] = user_id
    return args


class MCPToolRegistry:
    """Discovers MCP tools into a :class:`ToolRegistry` the filter can query."""

    def __init__(
        self,
        hub: MCPClientHub,
        configs: list[MCPServerConfig] | None = None,
        registry: ToolRegistry | None = None,
    ) -> None:
        self.hub = hub
        self.configs = configs if configs is not None else list(hub.servers.values())
        self.registry = registry or ToolRegistry()
        self.failed: dict[str, str] = {}
        self._by_name: dict[str, ToolSchema] = {}

    # ── construction ─────────────────────────────────────────
    @classmethod
    def from_config(
        cls, path: str | Path, registry: ToolRegistry | None = None, strict: bool = True
    ) -> MCPToolRegistry:
        """Build a hub + registry from ``mcp_servers.json``.

        With ``strict=False`` a missing or malformed config yields an empty
        registry instead of raising — the orchestrator should still boot and
        serve ``/health`` when its MCP config is broken.
        """
        try:
            configs = load_server_configs(path)
        except MCPConfigError as exc:
            if strict:
                raise
            log.error("mcp_config_unusable", path=str(path), error=str(exc))
            configs = []
        return cls(MCPClientHub(configs), configs, registry)

    # ── discovery ────────────────────────────────────────────
    async def discover(self) -> ToolRegistry:
        """Connect to every configured server and register its tools.

        Returns the populated registry. Failures are collected in
        :attr:`failed` rather than raised.
        """
        for config in self.configs:
            try:
                tools = await self.hub.list_tools(config.name)
            except Exception as exc:  # noqa: BLE001 - one bad server, not all
                self.failed[config.name] = f"{type(exc).__name__}: {exc}"
                log.warning("mcp_discovery_failed", server=config.name, error=str(exc))
                continue

            categories = config.categories or categories_for_server(config.name)
            if not categories:
                log.warning("mcp_server_uncategorized", server=config.name)
                categories = [DEFAULT_CATEGORY]

            for tool in tools:
                self._register(config.name, tool, categories)

            log.info(
                "mcp_server_discovered",
                server=config.name,
                tools=len(tools),
                categories=categories,
            )

        log.info(
            "mcp_discovery_complete",
            servers=len(self.configs),
            failed=sorted(self.failed),
            tools=len({t.name for t in self.registry.tools}),
        )
        return self.registry

    def _register(self, server: str, tool: dict[str, Any], categories: list[str]) -> None:
        """File one tool under each of its categories.

        The same tool object is registered once per category so that
        ``ToolRegistry.by_category`` finds it from either angle; the filter
        already de-duplicates by name when categories overlap.
        """
        name = qualified_name(server, tool["name"])
        for category in categories:
            schema = ToolSchema(
                name=name,
                server=server,
                category=category,
                description=tool.get("description", ""),
                input_schema=tool.get("input_schema", {}) or {},
            )
            self.registry.register(schema)
            self._by_name.setdefault(name, schema)

    # ── lookup + execution ───────────────────────────────────
    def resolve(self, name: str) -> tuple[str, str] | None:
        """Map a qualified or bare tool name back to ``(server, raw_tool_name)``.

        The LLM sometimes drops the server prefix (e.g. "memory_search" instead
        of "mcp-memory-scoped__memory_search"). We try exact match first, then
        fall back to any registered tool whose raw name matches.
        """
        schema = self._by_name.get(name)
        if schema is not None:
            return schema.server, name[len(schema.server) + len(QUALIFIER) :]
        # Bare-name fallback: find first registered tool ending with QUALIFIER+name
        suffix = QUALIFIER + name
        for qualified, s in self._by_name.items():
            if qualified.endswith(suffix):
                return s.server, name
        return None

    def get(self, name: str) -> ToolSchema | None:
        return self._by_name.get(name)

    @property
    def tool_names(self) -> list[str]:
        return sorted(self._by_name)

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute a qualified tool name through the hub.

        Returns the hub's envelope, including for an unknown tool — the model
        hallucinating a tool name is a tool failure like any other, and the
        graph should account for it the same way.

        The envelope's ``tool`` is rewritten to the qualified name. The hub
        reports the raw name because within one session that is the identity;
        outside it, circuit breakers and usage stats key on the qualified name,
        and two servers exposing ``search`` must not share a breaker.
        """
        resolved = self.resolve(name)
        if resolved is None:
            log.warning("mcp_unknown_tool", tool=name)
            return {"tool": name, "server": "unknown", "error": f"unknown tool: {name}"}

        server, raw = resolved
        self.registry.record_use(name)
        envelope = await self.hub.call_tool(server, raw, arguments)
        return {**envelope, "tool": name}

    async def health(self) -> dict[str, str]:
        """Per-server health, with discovery failures folded in."""
        statuses = await self.hub.health()
        for server, error in self.failed.items():
            statuses.setdefault(server, f"discovery_failed: {error}")
        return statuses

    async def close(self) -> None:
        await self.hub.close()
