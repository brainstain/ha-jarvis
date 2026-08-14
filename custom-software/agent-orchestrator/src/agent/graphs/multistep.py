"""Multi-step graph: plan -> execute step -> validate -> (more? / replan) -> synthesize.

Used for "plan dinner party", morning briefings, multi-device automations.
"""

from __future__ import annotations

from typing import Any, Callable

import structlog
from langgraph.graph import END, START, StateGraph

from agent.core.safety import SafetyGuard
from agent.graphs.state import AgentState

log = structlog.get_logger(__name__)


def build_multistep_graph(
    memory_lookup: Callable[[AgentState], Any],
    plan_steps: Callable[[AgentState], Any],
    execute_step: Callable[[AgentState], Any],
    synthesize: Callable[[AgentState], Any],
    guard: SafetyGuard,
    checkpointer: Any | None = None,
):
    """Compile the plan-then-execute workflow with a bounded replan loop."""

    async def memory_node(state: AgentState) -> dict[str, Any]:
        return {"memories": await memory_lookup(state)}

    async def plan_node(state: AgentState) -> dict[str, Any]:
        plan = await plan_steps(state)
        return {"plan": plan, "current_step": 0}

    async def execute_node(state: AgentState) -> dict[str, Any]:
        update = await execute_step(state)
        update["current_step"] = state.get("current_step", 0) + 1
        update["iteration_count"] = state.get("iteration_count", 0) + 1
        return update

    async def validate_node(state: AgentState) -> dict[str, Any]:
        calls = state.get("tool_calls", [])
        if calls:
            last = calls[-1]
            name = last.get("tool", "")
            if last.get("error"):
                guard.record_failure(name)
            else:
                guard.record_success(name)

        # Compress context if the chain is running hot on tokens.
        if guard.check_token_budget(state) == "compress" and not state.get("compressed"):
            return dict(guard.budget.compress_context(dict(state)))
        return {}

    async def synthesize_node(state: AgentState) -> dict[str, Any]:
        return await synthesize(state)

    def route_after_validate(state: AgentState) -> str:
        """Continue, stop, or fall through to synthesis."""
        if state.get("halted_reason"):
            return "synthesize"
        if not guard.check_iteration_limit(state):
            log.warning("iteration_limit_reached", thread_id=state.get("thread_id"))
            return "synthesize"
        if guard.check_token_budget(state) == "stop":
            log.warning("token_budget_exhausted", thread_id=state.get("thread_id"))
            return "synthesize"
        if guard.detect_loop(state):
            log.warning("loop_detected", thread_id=state.get("thread_id"))
            return "synthesize"
        if state.get("current_step", 0) < len(state.get("plan", [])):
            return "execute"
        return "synthesize"

    graph = StateGraph(AgentState)
    graph.add_node("memory_lookup", memory_node)
    graph.add_node("plan", plan_node)
    graph.add_node("execute", execute_node)
    graph.add_node("validate", validate_node)
    graph.add_node("synthesize", synthesize_node)

    graph.add_edge(START, "memory_lookup")
    graph.add_edge("memory_lookup", "plan")
    graph.add_edge("plan", "execute")
    graph.add_edge("execute", "validate")
    graph.add_conditional_edges(
        "validate",
        route_after_validate,
        {"execute": "execute", "synthesize": "synthesize"},
    )
    graph.add_edge("synthesize", END)

    return graph.compile(checkpointer=checkpointer)
