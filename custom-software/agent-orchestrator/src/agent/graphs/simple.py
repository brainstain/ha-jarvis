"""Simple command graph: memory lookup -> single tool -> validate -> respond.

Used for "turn off the lights", "what's the weather", "add milk to the list".
"""

from __future__ import annotations

from typing import Any, Callable

import structlog
from langgraph.graph import END, START, StateGraph

from agent.core.safety import SafetyGuard
from agent.graphs.state import AgentState

log = structlog.get_logger(__name__)


def build_simple_graph(
    memory_lookup: Callable[[AgentState], Any],
    execute_tool: Callable[[AgentState], Any],
    synthesize: Callable[[AgentState], Any],
    guard: SafetyGuard,
    checkpointer: Any | None = None,
):
    """Compile the simple-command workflow.

    Node callables are injected so the graph is testable without live MCP
    servers or an LLM.
    """

    async def memory_node(state: AgentState) -> dict[str, Any]:
        memories = await memory_lookup(state)
        return {"memories": memories, "iteration_count": state.get("iteration_count", 0) + 1}

    async def execute_node(state: AgentState) -> dict[str, Any]:
        if not guard.check_iteration_limit(state):
            return {"halted_reason": "iteration_limit"}
        if guard.detect_loop(state):
            return {"halted_reason": "loop_detected"}
        return await execute_tool(state)

    async def validate_node(state: AgentState) -> dict[str, Any]:
        """Record tool outcome for circuit-breaker accounting."""
        calls = state.get("tool_calls", [])
        if calls:
            last = calls[-1]
            name = last.get("tool", "")
            if last.get("error"):
                guard.record_failure(name)
            else:
                guard.record_success(name)
        return {}

    async def respond_node(state: AgentState) -> dict[str, Any]:
        if state.get("halted_reason"):
            return {
                "response": "I had trouble completing that — let's try again.",
                "confidence": 0.3,
            }
        return await synthesize(state)

    graph = StateGraph(AgentState)
    graph.add_node("memory_lookup", memory_node)
    graph.add_node("execute", execute_node)
    graph.add_node("validate", validate_node)
    graph.add_node("respond", respond_node)

    graph.add_edge(START, "memory_lookup")
    graph.add_edge("memory_lookup", "execute")
    graph.add_edge("execute", "validate")
    graph.add_edge("validate", "respond")
    graph.add_edge("respond", END)

    return graph.compile(checkpointer=checkpointer)
