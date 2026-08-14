"""MCP client hub and tool registry.

Everything here runs against fake sessions — the point is to pin the hub's
contract (config validation, lazy caching, envelopes, fault isolation) without
spawning subprocesses or needing Home Assistant on the network.
"""

import asyncio
import json
from pathlib import Path

import pytest

from agent.mcp.client import (
    MCPConfigError,
    MCPServerConfig,
    flatten_result,
    load_server_configs,
)
from agent.mcp.registry import (
    MCPToolRegistry,
    categories_for_server,
    qualified_name,
    render_openai_tools,
)
from tests.mcp_fakes import (
    CamelTool,
    FakeCallResult,
    FakeHub,
    FakeSession,
    FakeTool,
    ImageBlock,
    TextBlock,
)


def sse(name="ha-mcp", **kw):
    return MCPServerConfig(name=name, transport="sse", url="http://ha/mcp", **kw)


# ──────────────────────────────────────────────────────────────────────
# Config validation
# ──────────────────────────────────────────────────────────────────────


def test_stdio_config_parses():
    config = MCPServerConfig.from_dict(
        {
            "name": "mcp-memory-scoped",
            "transport": "stdio",
            "command": "python",
            "args": ["-m", "mcp_memory_scoped"],
            "categories": ["memory"],
        }
    )
    assert config.transport == "stdio"
    assert config.args == ["-m", "mcp_memory_scoped"]
    assert config.enabled is True


def test_spec_style_keys_are_accepted():
    """The SPEC.md registry uses `type` and singular `category`."""
    config = MCPServerConfig.from_dict(
        {
            "name": "home_assistant",
            "type": "sse",
            "url": "http://gateway.home.local:8123/mcp",
            "category": "home_automation",
            "always_available": True,
        }
    )
    assert config.transport == "sse"
    assert config.categories == ["home_automation"]
    assert config.enabled is True


@pytest.mark.parametrize(
    "entry",
    [
        {"transport": "stdio", "command": "python"},  # no name
        {"name": "x", "transport": "carrier-pigeon"},  # bad transport
        {"name": "x", "transport": "stdio"},  # stdio without command
        {"name": "x", "transport": "sse"},  # sse without url
        {"name": "x", "transport": "stdio", "command": "py", "categories": "memory"},
        {"name": "x", "transport": "stdio", "command": "py", "args": "nope"},
        "not-an-object",
    ],
)
def test_invalid_server_entries_rejected(entry):
    with pytest.raises(MCPConfigError):
        MCPServerConfig.from_dict(entry)


def test_shipped_config_is_valid():
    """config/mcp_servers.json must actually load — it ships in the image."""
    repo_config = Path(__file__).resolve().parents[2] / "config" / "mcp_servers.json"
    enabled = load_server_configs(repo_config)
    assert [c.name for c in enabled] == ["ha-mcp"]
    assert enabled[0].transport == "sse"


# ──────────────────────────────────────────────────────────────────────
# Config loading: disabled exclusion, missing / invalid files
# ──────────────────────────────────────────────────────────────────────


def write_config(tmp_path, payload):
    path = tmp_path / "mcp_servers.json"
    path.write_text(json.dumps(payload) if not isinstance(payload, str) else payload)
    return path


def test_disabled_servers_are_excluded(tmp_path):
    path = write_config(
        tmp_path,
        {
            "servers": [
                {"name": "ha-mcp", "transport": "sse", "url": "http://ha/mcp", "enabled": True},
                {
                    "name": "mcp-notifications",
                    "transport": "stdio",
                    "command": "python",
                    "enabled": False,
                },
            ]
        },
    )
    assert [c.name for c in load_server_configs(path)] == ["ha-mcp"]


def test_missing_config_file_raises(tmp_path):
    with pytest.raises(MCPConfigError):
        load_server_configs(tmp_path / "nope.json")


def test_invalid_json_raises(tmp_path):
    with pytest.raises(MCPConfigError):
        load_server_configs(write_config(tmp_path, "{not json"))


def test_config_without_servers_array_raises(tmp_path):
    with pytest.raises(MCPConfigError):
        load_server_configs(write_config(tmp_path, {"tool_routing": {}}))


def test_duplicate_server_names_rejected(tmp_path):
    payload = {
        "servers": [
            {"name": "dup", "transport": "sse", "url": "http://a"},
            {"name": "dup", "transport": "sse", "url": "http://b"},
        ]
    }
    with pytest.raises(MCPConfigError):
        load_server_configs(write_config(tmp_path, payload))


def test_from_config_non_strict_survives_a_broken_file(tmp_path):
    """A bad MCP config must degrade tool use, not stop the service booting."""
    mcp = MCPToolRegistry.from_config(write_config(tmp_path, "{oops"), strict=False)
    assert mcp.configs == []
    assert mcp.tool_names == []


def test_from_config_strict_raises(tmp_path):
    with pytest.raises(MCPConfigError):
        MCPToolRegistry.from_config(write_config(tmp_path, "{oops"), strict=True)


# ──────────────────────────────────────────────────────────────────────
# Connection caching
# ──────────────────────────────────────────────────────────────────────


async def test_connection_is_cached():
    hub = FakeHub([sse()])
    first = await hub.connect("ha-mcp")
    second = await hub.connect("ha-mcp")
    assert first is second
    assert hub.open_count["ha-mcp"] == 1


async def test_concurrent_calls_open_one_connection():
    """Per-server lock: ten simultaneous requests must not spawn ten servers."""
    hub = FakeHub([sse()])
    await asyncio.gather(*(hub.connect("ha-mcp") for _ in range(10)))
    assert hub.open_count["ha-mcp"] == 1


async def test_unknown_server_is_a_config_error():
    with pytest.raises(MCPConfigError):
        await FakeHub([sse()]).connect("does-not-exist")


async def test_failed_connect_is_not_cached():
    hub = FakeHub([sse()], open_errors={"ha-mcp": RuntimeError("boom")})
    with pytest.raises(RuntimeError):
        await hub.connect("ha-mcp")
    assert "ha-mcp" not in hub._sessions
    assert "ha-mcp" not in hub._stacks


# ──────────────────────────────────────────────────────────────────────
# Result flattening
# ──────────────────────────────────────────────────────────────────────


def test_flatten_prefers_structured_content():
    result = FakeCallResult([TextBlock("ignored")], structured_content={"temp": 21})
    assert flatten_result(result) == {"temp": 21}


def test_flatten_single_text_block_to_string():
    assert flatten_result(FakeCallResult([TextBlock("living room is 21C")])) == "living room is 21C"


def test_flatten_joins_multiple_text_blocks():
    result = FakeCallResult([TextBlock("a"), TextBlock("b")])
    assert flatten_result(result) == "a\nb"


def test_flatten_empty_content_is_empty_string():
    assert flatten_result(FakeCallResult([])) == ""


def test_flatten_mixed_content_keeps_structure():
    result = FakeCallResult([TextBlock("chart:"), ImageBlock("xyz")])
    assert flatten_result(result) == ["chart:", {"type": "image", "data": "xyz"}]


# ──────────────────────────────────────────────────────────────────────
# call_tool envelopes
# ──────────────────────────────────────────────────────────────────────


async def test_call_tool_success_envelope():
    session = FakeSession(results={"light_off": FakeCallResult([TextBlock("done")])})
    hub = FakeHub([sse()], sessions={"ha-mcp": session})
    envelope = await hub.call_tool("ha-mcp", "light_off", {"entity": "light.kitchen"})
    assert envelope == {"tool": "light_off", "server": "ha-mcp", "result": "done"}
    assert session.calls == [("light_off", {"entity": "light.kitchen"})]


async def test_call_tool_returns_error_envelope_instead_of_raising():
    session = FakeSession(results={"light_off": RuntimeError("transport closed")})
    hub = FakeHub([sse()], sessions={"ha-mcp": session})
    envelope = await hub.call_tool("ha-mcp", "light_off")
    assert envelope["tool"] == "light_off"
    assert "transport closed" in envelope["error"]
    assert "result" not in envelope


async def test_is_error_result_becomes_an_error_envelope():
    """A tool that says no is a failure for circuit-breaker purposes."""
    session = FakeSession(
        results={"light_off": FakeCallResult([TextBlock("no such entity")], is_error=True)}
    )
    hub = FakeHub([sse()], sessions={"ha-mcp": session})
    envelope = await hub.call_tool("ha-mcp", "light_off")
    assert envelope["error"] == "no such entity"


async def test_call_tool_times_out_into_an_envelope():
    async def hang(_args):
        await asyncio.sleep(10)

    session = FakeSession(results={"slow": hang})
    hub = FakeHub([sse(timeout=0.05)], sessions={"ha-mcp": session})
    envelope = await hub.call_tool("ha-mcp", "slow")
    assert "timeout" in envelope["error"]


async def test_connection_failure_surfaces_as_an_envelope():
    hub = FakeHub([sse()], open_errors={"ha-mcp": ConnectionRefusedError("no route")})
    envelope = await hub.call_tool("ha-mcp", "light_off")
    assert envelope["error"].startswith("ConnectionRefusedError")


# ──────────────────────────────────────────────────────────────────────
# Discovery
# ──────────────────────────────────────────────────────────────────────


def build_registry(configs, sessions, **kw):
    hub = FakeHub(configs, sessions=sessions, **kw)
    return MCPToolRegistry(hub, configs)


async def test_discovery_registers_tools_with_schemas():
    sessions = {
        "ha-mcp": FakeSession(
            tools=[
                FakeTool("light_turn_on", "Turn a light on", {"type": "object"}),
                FakeTool("climate_set", "Set the thermostat"),
            ]
        )
    }
    mcp = build_registry([sse(categories=["home_assistant"])], sessions)
    registry = await mcp.discover()

    assert mcp.tool_names == ["ha-mcp__climate_set", "ha-mcp__light_turn_on"]
    tool = mcp.get("ha-mcp__light_turn_on")
    assert tool.server == "ha-mcp"
    assert tool.category == "home_assistant"
    assert tool.description == "Turn a light on"
    assert registry.by_category("home_assistant")


async def test_discovery_reads_camelcase_input_schema():
    """mcp 1.x spelled it inputSchema; pyproject pins only mcp>=1.0."""
    sessions = {"ha-mcp": FakeSession(tools=[CamelTool("ping", "p", {"type": "object"})])}
    mcp = build_registry([sse(categories=["home_assistant"])], sessions)
    await mcp.discover()
    assert mcp.get("ha-mcp__ping").input_schema == {"type": "object"}


async def test_one_broken_server_does_not_block_the_others():
    configs = [
        sse(name="ha-mcp", categories=["home_assistant"]),
        MCPServerConfig(
            name="mcp-memory-scoped",
            transport="stdio",
            command="python",
            categories=["memory"],
        ),
    ]
    sessions = {
        "ha-mcp": FakeSession(tools=[FakeTool("light_turn_on")]),
        "mcp-memory-scoped": FakeSession(fail_on_list=RuntimeError("server crashed")),
    }
    mcp = build_registry(configs, sessions)
    await mcp.discover()

    assert mcp.tool_names == ["ha-mcp__light_turn_on"]
    assert "mcp-memory-scoped" in mcp.failed
    assert "server crashed" in mcp.failed["mcp-memory-scoped"]


async def test_server_that_will_not_start_is_skipped():
    configs = [sse(name="ha-mcp", categories=["home_assistant"]), sse(name="other")]
    mcp = build_registry(
        configs,
        {"ha-mcp": FakeSession(tools=[FakeTool("light_turn_on")])},
        open_errors={"other": FileNotFoundError("npx not found")},
    )
    await mcp.discover()
    assert mcp.tool_names == ["ha-mcp__light_turn_on"]
    assert "other" in mcp.failed


# ──────────────────────────────────────────────────────────────────────
# Category mapping
# ──────────────────────────────────────────────────────────────────────


def test_category_fallback_reads_the_tool_filter_map():
    assert categories_for_server("ha-mcp") == ["home_assistant"]
    assert categories_for_server("mcp-filesystem") == ["documents", "filesystem"]
    assert categories_for_server("who-dis") == []


async def test_server_without_explicit_categories_falls_back_to_the_map():
    configs = [
        MCPServerConfig(name="mcp-filesystem", transport="stdio", command="npx"),
    ]
    mcp = build_registry(configs, {"mcp-filesystem": FakeSession(tools=[FakeTool("read_file")])})
    registry = await mcp.discover()

    name = qualified_name("mcp-filesystem", "read_file")
    assert [t.name for t in registry.by_category("documents")] == [name]
    assert [t.name for t in registry.by_category("filesystem")] == [name]


async def test_explicit_categories_override_the_map():
    configs = [
        MCPServerConfig(
            name="mcp-filesystem", transport="stdio", command="npx", categories=["documents"]
        )
    ]
    mcp = build_registry(configs, {"mcp-filesystem": FakeSession(tools=[FakeTool("read_file")])})
    registry = await mcp.discover()
    assert registry.by_category("documents")
    assert registry.by_category("filesystem") == []


async def test_uncategorized_server_still_registers():
    configs = [MCPServerConfig(name="mystery", transport="stdio", command="python")]
    mcp = build_registry(configs, {"mystery": FakeSession(tools=[FakeTool("do_thing")])})
    registry = await mcp.discover()
    assert [t.category for t in registry.tools] == ["other"]


async def test_multi_category_tool_is_selected_once():
    """Registered under two categories, but the max-7 filter must not double it."""
    from agent.mcp.tool_filter import ToolFilter

    configs = [MCPServerConfig(name="mcp-filesystem", transport="stdio", command="npx")]
    mcp = build_registry(configs, {"mcp-filesystem": FakeSession(tools=[FakeTool("read_file")])})
    registry = await mcp.discover()

    selected = ToolFilter(registry).select_tools(["documents", "filesystem"], max_tools=7)
    assert [t.name for t in selected] == ["mcp-filesystem__read_file"]


# ──────────────────────────────────────────────────────────────────────
# Execution through the registry
# ──────────────────────────────────────────────────────────────────────


async def test_registry_call_resolves_qualified_names():
    session = FakeSession(
        tools=[FakeTool("light_turn_on")],
        results={"light_turn_on": FakeCallResult([TextBlock("on")])},
    )
    mcp = build_registry([sse(categories=["home_assistant"])], {"ha-mcp": session})
    await mcp.discover()

    envelope = await mcp.call("ha-mcp__light_turn_on", {"entity_id": "light.kitchen"})
    # Qualified name in the envelope: circuit breakers key on it, and two
    # servers with the same raw tool name must not share one.
    assert envelope == {"tool": "ha-mcp__light_turn_on", "server": "ha-mcp", "result": "on"}
    assert session.calls == [("light_turn_on", {"entity_id": "light.kitchen"})]
    assert mcp.registry.usage("ha-mcp__light_turn_on") == 1


async def test_hallucinated_tool_name_returns_an_error_envelope():
    mcp = build_registry([sse(categories=["home_assistant"])], {"ha-mcp": FakeSession()})
    await mcp.discover()
    envelope = await mcp.call("ha-mcp__teleport", {})
    assert "unknown tool" in envelope["error"]


async def test_two_servers_can_expose_the_same_tool_name():
    configs = [
        sse(name="ha-mcp", categories=["home_assistant"]),
        MCPServerConfig(
            name="mcp-memory-scoped", transport="stdio", command="python", categories=["memory"]
        ),
    ]
    sessions = {
        "ha-mcp": FakeSession(
            tools=[FakeTool("search")], results={"search": FakeCallResult([TextBlock("ha")])}
        ),
        "mcp-memory-scoped": FakeSession(
            tools=[FakeTool("search")], results={"search": FakeCallResult([TextBlock("mem")])}
        ),
    }
    mcp = build_registry(configs, sessions)
    await mcp.discover()

    assert sorted(mcp.tool_names) == ["ha-mcp__search", "mcp-memory-scoped__search"]
    ha = await mcp.call("ha-mcp__search")
    mem = await mcp.call("mcp-memory-scoped__search")
    assert (ha["tool"], ha["result"]) == ("ha-mcp__search", "ha")
    assert (mem["tool"], mem["result"]) == ("mcp-memory-scoped__search", "mem")


# ──────────────────────────────────────────────────────────────────────
# Health
# ──────────────────────────────────────────────────────────────────────


async def test_health_pings_every_server():
    configs = [sse(name="ha-mcp"), sse(name="broken")]
    hub = FakeHub(
        configs,
        sessions={"ha-mcp": FakeSession(tools=[FakeTool("x")])},
        open_errors={"broken": ConnectionRefusedError("nope")},
    )
    statuses = await hub.health()
    assert statuses["ha-mcp"] == "ok"
    assert statuses["broken"].startswith("unreachable")


async def test_health_with_no_servers_is_empty():
    assert await FakeHub([]).health() == {}


async def test_registry_health_reports_discovery_failures():
    configs = [sse(name="ha-mcp")]
    mcp = build_registry(configs, {"ha-mcp": FakeSession(fail_on_list=RuntimeError("crash"))})
    await mcp.discover()
    statuses = await mcp.health()
    assert statuses["ha-mcp"].startswith("unreachable")


# ──────────────────────────────────────────────────────────────────────
# OpenAI / LiteLLM rendering
# ──────────────────────────────────────────────────────────────────────


async def test_render_openai_tools():
    schema = {
        "type": "object",
        "properties": {"entity_id": {"type": "string"}},
        "required": ["entity_id"],
    }
    sessions = {"ha-mcp": FakeSession(tools=[FakeTool("light_turn_on", "Turn on a light", schema)])}
    mcp = build_registry([sse(categories=["home_assistant"])], sessions)
    registry = await mcp.discover()

    rendered = render_openai_tools(registry.by_category("home_assistant"))
    assert rendered == [
        {
            "type": "function",
            "function": {
                "name": "ha-mcp__light_turn_on",
                "description": "Turn on a light",
                "parameters": schema,
            },
        }
    ]


def test_render_supplies_parameters_for_schemaless_tools():
    from agent.mcp.tool_filter import ToolSchema

    rendered = render_openai_tools(
        [ToolSchema(name="ha-mcp__ping", server="ha-mcp", category="home_assistant")]
    )
    assert rendered[0]["function"]["parameters"] == {"type": "object", "properties": {}}
    # Empty descriptions still tell the model where the tool came from.
    assert "ha-mcp" in rendered[0]["function"]["description"]


def test_rendered_names_are_valid_openai_function_names():
    import re

    from agent.mcp.tool_filter import ToolSchema

    rendered = render_openai_tools(
        [
            ToolSchema(
                name=qualified_name("mcp-memory-scoped", "memory_search"),
                server="x",
                category="memory",
            )
        ]
    )
    assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", rendered[0]["function"]["name"])
