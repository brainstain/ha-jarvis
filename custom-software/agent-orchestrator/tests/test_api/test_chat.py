"""/chat and /health wiring: routing -> tool selection -> MCP execution."""

import json

import pytest

from agent.api import routes
from agent.api.schemas import ChatRequest, RoutingDecision
from agent.mcp.client import MCPServerConfig
from agent.mcp.registry import MCPToolRegistry
from tests.mcp_fakes import FakeCallResult, FakeHub, FakeSession, FakeTool, TextBlock


def tool_call(name, arguments):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


class ScriptedLLM:
    """Returns queued assistant messages; records the payloads it was given."""

    def __init__(self, *messages):
        self.queue = list(messages)
        self.tools_seen = []

    async def complete(self, messages, tools=None, **kwargs):
        self.tools_seen.append(tools)
        if not self.queue:
            return {"role": "assistant", "content": "done"}
        outcome = self.queue.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def ha_registry():
    """A discovered registry backed by a fake ha-mcp with two tools."""
    configs = [
        MCPServerConfig(
            name="ha-mcp",
            transport="sse",
            url="http://ha/mcp",
            categories=["home_assistant"],
        )
    ]
    session = FakeSession(
        tools=[FakeTool("light_turn_off", "Turn a light off"), FakeTool("climate_set")],
        results={"light_turn_off": FakeCallResult([TextBlock("light.kitchen is off")])},
    )
    return MCPToolRegistry(FakeHub(configs, sessions={"ha-mcp": session}), configs), session


@pytest.fixture(autouse=True)
def clean_tools():
    yield
    routes.set_tools(None)


def install(decision, llm, mcp=None, monkeypatch=None):
    async def route(message, context):
        return decision

    monkeypatch.setattr(routes.meta_router, "route", route)
    monkeypatch.setattr(routes, "llm", llm)
    routes.set_tools(mcp)


def request(**overrides):
    base = {
        "message": "turn off the kitchen light",
        "user_id": "michael",
        "scope": "family",
        "source": "webui",
    }
    base.update(overrides)
    return ChatRequest(**base)


SIMPLE = RoutingDecision(
    intent="command",
    graph="simple",
    tools_needed=["home_assistant"],
    execution_mode="sync",
    output_channel="webui",
)


async def test_simple_graph_executes_a_tool_through_the_hub(ha_registry, monkeypatch):
    mcp, session = ha_registry
    await mcp.discover()
    llm = ScriptedLLM(
        tool_call("ha-mcp__light_turn_off", {"entity_id": "light.kitchen"}),
        {"role": "assistant", "content": "The kitchen light is off."},
    )
    install(SIMPLE, llm, mcp, monkeypatch)

    response = await routes.chat(request())

    assert response.message == "The kitchen light is off."
    assert response.tools_used == ["ha-mcp__light_turn_off"]
    assert response.confidence == 0.9
    assert session.calls == [("light_turn_off", {"entity_id": "light.kitchen"})]
    # The model only ever saw the home_assistant tools, rendered OpenAI-style.
    names = [t["function"]["name"] for t in llm.tools_seen[0]]
    assert names == ["ha-mcp__climate_set", "ha-mcp__light_turn_off"]


async def test_failed_tool_call_trips_the_circuit_breaker(ha_registry, monkeypatch):
    mcp, session = ha_registry
    session.results["light_turn_off"] = RuntimeError("HA unreachable")
    await mcp.discover()
    llm = ScriptedLLM(
        tool_call("ha-mcp__light_turn_off", {}),
        {"role": "assistant", "content": "I couldn't reach the light."},
    )
    install(SIMPLE, llm, mcp, monkeypatch)
    monkeypatch.setattr(routes.guard, "circuit_breaker_threshold", 1)

    response = await routes.chat(request())

    assert response.confidence == 0.4
    assert routes.guard.check_circuit_breaker("ha-mcp__light_turn_off") is True
    routes.guard.record_success("ha-mcp__light_turn_off")  # reset shared guard


async def test_open_circuit_hides_the_tool_from_the_model(ha_registry, monkeypatch):
    mcp, _session = ha_registry
    await mcp.discover()
    llm = ScriptedLLM({"role": "assistant", "content": "I can't do that right now."})
    install(SIMPLE, llm, mcp, monkeypatch)

    routes.guard.record_failure("ha-mcp__light_turn_off")
    routes.guard.record_failure("ha-mcp__light_turn_off")
    routes.guard.record_failure("ha-mcp__light_turn_off")
    try:
        await routes.chat(request())
        names = [t["function"]["name"] for t in llm.tools_seen[0]]
        assert names == ["ha-mcp__climate_set"]
    finally:
        routes.guard.record_success("ha-mcp__light_turn_off")


async def test_no_tools_still_answers(monkeypatch):
    """Nothing discovered: the graph should answer, not 501 and not crash."""
    llm = ScriptedLLM({"role": "assistant", "content": "I don't have that connected yet."})
    install(SIMPLE, llm, None, monkeypatch)

    response = await routes.chat(request())

    assert response.message == "I don't have that connected yet."
    assert response.tools_used == []
    assert response.confidence == 0.7


async def test_llm_outage_degrades_instead_of_500(ha_registry, monkeypatch):
    import httpx

    mcp, _session = ha_registry
    await mcp.discover()
    llm = ScriptedLLM(httpx.ConnectError("litellm down"), httpx.ConnectError("litellm down"))
    install(SIMPLE, llm, mcp, monkeypatch)

    response = await routes.chat(request())

    assert response.confidence == 0.3
    assert response.message


async def test_voice_requests_get_the_speech_prompt(ha_registry, monkeypatch):
    mcp, _session = ha_registry
    await mcp.discover()
    llm = ScriptedLLM(
        {"role": "assistant", "content": None},
        {"role": "assistant", "content": "Kitchen light off."},
    )
    install(SIMPLE, llm, mcp, monkeypatch)

    response = await routes.chat(request(source="voice", satellite_id="kitchen"))

    assert response.output_channel == "voice"


async def test_unwired_graphs_still_return_501(monkeypatch):
    llm = ScriptedLLM()
    decision = RoutingDecision(
        intent="diagnostic", graph="interactive", tools_needed=[], execution_mode="sync"
    )
    install(decision, llm, None, monkeypatch)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        await routes.chat(request())
    assert excinfo.value.status_code == 501


async def test_health_reports_mcp_status(ha_registry, monkeypatch):
    mcp, _session = ha_registry
    await mcp.discover()
    routes.set_tools(mcp)

    response = await routes.health()

    assert response.checks["mcp"] == "ok"
    assert response.checks["mcp:ha-mcp"] == "ok"
    assert response.checks["mcp_tools"] == "2"


async def test_health_says_unconfigured_without_mcp():
    routes.set_tools(None)
    response = await routes.health()
    assert response.checks["mcp"] == "unconfigured"
