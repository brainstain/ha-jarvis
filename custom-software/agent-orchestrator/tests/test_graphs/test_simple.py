"""Simple command graph execution."""

import pytest

from agent.core.safety import SafetyGuard
from agent.graphs.simple import build_simple_graph


async def memory_lookup(state):
    return [{"text": "kitchen light is light.kitchen"}]


async def execute_tool(state):
    return {
        "tool_calls": [{"tool": "ha.light_off", "args": {"entity": "light.kitchen"}}],
        "tools_used": ["ha.light_off"],
    }


async def synthesize(state):
    return {"response": "Kitchen light is off.", "confidence": 0.95}


@pytest.mark.asyncio
async def test_simple_graph_happy_path():
    graph = build_simple_graph(memory_lookup, execute_tool, synthesize, SafetyGuard())
    result = await graph.ainvoke(
        {"message": "turn off the kitchen light", "user_id": "michael", "scope": "family"}
    )
    assert result["response"] == "Kitchen light is off."
    assert result["tools_used"] == ["ha.light_off"]
    assert result["memories"][0]["text"].startswith("kitchen light")


@pytest.mark.asyncio
async def test_simple_graph_halts_on_iteration_limit():
    guard = SafetyGuard(max_iterations=1)
    graph = build_simple_graph(memory_lookup, execute_tool, synthesize, guard)
    # memory_lookup increments to 1, so execute sees the limit already reached.
    result = await graph.ainvoke({"message": "x", "user_id": "u", "scope": "family"})
    assert result["halted_reason"] == "iteration_limit"
    assert result["confidence"] == 0.3


@pytest.mark.asyncio
async def test_failed_tool_trips_circuit_breaker():
    guard = SafetyGuard(circuit_breaker_threshold=1)

    async def failing_tool(state):
        return {"tool_calls": [{"tool": "ha.light_off", "args": {}, "error": "timeout"}]}

    graph = build_simple_graph(memory_lookup, failing_tool, synthesize, guard)
    await graph.ainvoke({"message": "x", "user_id": "u", "scope": "family"})
    assert guard.check_circuit_breaker("ha.light_off") is True
