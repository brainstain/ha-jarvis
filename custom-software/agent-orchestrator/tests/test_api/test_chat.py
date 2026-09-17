"""/chat and /health wiring: routing -> tool selection -> MCP execution."""

import json

import pytest

from agent.api import routes
from agent.api.schemas import ChatRequest, RoutingDecision
from agent.mcp.client import MCPServerConfig
from agent.mcp.registry import MCPToolRegistry
from tests.mcp_fakes import FakeCallResult, FakeHub, FakeSession, FakeTool, TextBlock


def tool_call(name, arguments):
    """A grammar-constrained tool-selection response (see registry.py's
    render_tool_selection_schema / parse_tool_selection) — not native
    OpenAI-style tool_calls, which don't reliably work on this stack."""
    return {
        "role": "assistant",
        "content": json.dumps({"tool": name, "arguments": arguments}),
    }


class ScriptedLLM:
    """Returns queued assistant messages; records the payloads it was given."""

    def __init__(self, *messages):
        self.queue = list(messages)
        self.tools_seen = []

    async def complete(self, messages, extra_body=None, **kwargs):
        self.tools_seen.append(extra_body)
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
    # The model only ever saw the home_assistant tools, enum-constrained
    # into the grammar-selection schema (see render_tool_selection_schema).
    names = [n for n in llm.tools_seen[0]["format"]["properties"]["tool"]["enum"] if n != "none"]
    assert names == ["ha-mcp__climate_set", "ha-mcp__light_turn_off"]


async def test_synthesis_disables_thinking(ha_registry, monkeypatch):
    """Regression: synthesis previously left `think` unset (effectively
    think:true), which frequently made the model answer from its own
    inline reasoning instead of the tool result — confirmed live, 3 of 4
    identical trials against a known tool result fabricated a plausible
    but wrong answer instead of reporting what the tool actually returned.
    think:False must be passed on the synthesis call.
    """
    mcp, session = ha_registry
    await mcp.discover()
    llm = ScriptedLLM(
        tool_call("ha-mcp__light_turn_off", {"entity_id": "light.kitchen"}),
        {"role": "assistant", "content": "The kitchen light is off."},
    )
    install(SIMPLE, llm, mcp, monkeypatch)

    await routes.chat(request())

    synthesis_extra_body = llm.tools_seen[1]
    assert synthesis_extra_body == {"think": False}


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
        names = [n for n in llm.tools_seen[0]["format"]["properties"]["tool"]["enum"] if n != "none"]
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


async def test_unknown_graph_returns_501(monkeypatch):
    """routes.chat's final else-branch: any graph value other than
    simple/multistep/interactive still 501s. In practice the router only
    ever pairs graph="research" with execution_mode="async" (intercepted
    earlier, at the top of chat()), so this exercises the defensive case
    of that pairing being violated rather than a reachable router output.
    """
    llm = ScriptedLLM()
    decision = RoutingDecision(
        intent="research", graph="research", tools_needed=[], execution_mode="sync"
    )
    install(decision, llm, None, monkeypatch)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        await routes.chat(request())
    assert excinfo.value.status_code == 501


async def test_interactive_graph_degrades_gracefully_without_mcp(monkeypatch, tmp_path):
    """Regression: the interactive graph IS wired (unlike the name of the
    test this replaces implied) and runs even with mcp=None — it must not
    crash. It used to: the no-MCP tool-executor fallback was a plain sync
    `lambda s: {}`, and diagnose_node always `await`s the injected
    executor, so hitting this exact path raised
    TypeError("object dict can't be used in 'await' expression") instead
    of answering. See agent.api.routes._no_tools_executor.
    """
    monkeypatch.setattr(routes.settings, "langgraph_db", str(tmp_path / "checkpoints.db"))
    llm = ScriptedLLM({"role": "assistant", "content": "I can't do that right now."})
    decision = RoutingDecision(
        intent="diagnostic", graph="interactive", tools_needed=[], execution_mode="sync"
    )
    install(decision, llm, None, monkeypatch)

    response = await routes.chat(request())

    assert response.message


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
