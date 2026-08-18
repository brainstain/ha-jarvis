"""Interactive (HITL) graph: diagnose → may pause for user input → resume → respond.

Used for multi-step diagnostic flows where the agent needs to ask the user a
clarifying question mid-execution (e.g. "which device did you mean?", "confirm
turning off all lights?").

LangGraph's ``interrupt_before`` on the ``await_input`` node causes the graph
to save state to the checkpointer and return. The orchestrator stores the
paused thread_id, responds with the question, and later resumes via
``graph.ainvoke(Command(resume=user_answer), config)``.
"""

from __future__ import annotations

from typing import Any, Callable

import structlog
from langgraph.graph import END, START, StateGraph

from agent.core.safety import SafetyGuard
from agent.graphs.state import AgentState

log = structlog.get_logger(__name__)

# Strings in a tool result that signal the agent needs clarification before acting.
_CLARIFICATION_SIGNALS = (
    "which",
    "which one",
    "confirm",
    "are you sure",
    "do you want",
    "needs_confirmation",
)


def build_interactive_graph(
    memory_lookup: Callable[[AgentState], Any],
    tool_executor: Callable[[AgentState], Any],
    synthesize: Callable[[AgentState], Any],
    guard: SafetyGuard,
    checkpointer: Any | None = None,
):
    """Compile the HITL diagnostic graph.

    The graph will interrupt *before* ``await_input`` if a tool result signals
    that user clarification is required. The caller catches the interrupt,
    sends back the question, and calls ``resume`` with the user's answer.
    """

    async def memory_node(state: AgentState) -> dict[str, Any]:
        memories = await memory_lookup(state)
        return {"memories": memories}

    async def diagnose_node(state: AgentState) -> dict[str, Any]:
        if not guard.check_iteration_limit(state):
            return {"halted_reason": "iteration_limit"}
        if guard.detect_loop(state):
            return {"halted_reason": "loop_detected"}
        update = await tool_executor(state)
        return {**update, "iteration_count": state.get("iteration_count", 0) + 1}

    async def validate_node(state: AgentState) -> dict[str, Any]:
        calls = state.get("tool_calls") or []
        if calls:
            last = calls[-1]
            name = last.get("tool", "")
            if last.get("error"):
                guard.record_failure(name)
            else:
                guard.record_success(name)
        return {}

    async def await_input_node(state: AgentState) -> dict[str, Any]:
        """Extract the clarifying question from the last tool result."""
        calls = state.get("tool_calls") or []
        last = calls[-1] if calls else {}
        result = last.get("result", {})
        if isinstance(result, dict):
            question = result.get("question") or result.get("message") or "Could you clarify?"
        else:
            question = str(result) if result else "Could you clarify?"
        return {"pending_question": question}

    async def resume_node(state: AgentState) -> dict[str, Any]:
        """Inject the user's answer back into the conversation."""
        answer = state.get("user_response") or ""
        msgs = list(state.get("messages") or [])
        if answer:
            msgs.append({"role": "user", "content": answer})
        return {"messages": msgs, "pending_question": None, "user_response": None}

    async def respond_node(state: AgentState) -> dict[str, Any]:
        if state.get("halted_reason"):
            return {
                "response": "I ran into a problem — can you rephrase that?",
                "confidence": 0.2,
            }
        if state.get("pending_question"):
            return {"response": state["pending_question"], "confidence": 0.5}
        return await synthesize(state)

    def route_after_diagnose(state: AgentState) -> str:
        if state.get("halted_reason"):
            return "respond"
        calls = state.get("tool_calls") or []
        if not calls:
            return "respond"
        last = calls[-1]
        result = last.get("result")
        # Detect confirmation requests from tool results
        if isinstance(result, dict) and (
            result.get("needs_confirmation") or result.get("question")
        ):
            return "await_input"
        if isinstance(result, str):
            lower = result.lower()
            if any(sig in lower for sig in _CLARIFICATION_SIGNALS):
                return "await_input"
        return "validate"

    graph = StateGraph(AgentState)
    graph.add_node("memory_lookup", memory_node)
    graph.add_node("diagnose", diagnose_node)
    graph.add_node("validate", validate_node)
    graph.add_node("await_input", await_input_node)
    graph.add_node("resume", resume_node)
    graph.add_node("respond", respond_node)

    graph.add_edge(START, "memory_lookup")
    graph.add_edge("memory_lookup", "diagnose")
    graph.add_conditional_edges(
        "diagnose",
        route_after_diagnose,
        {"validate": "validate", "respond": "respond", "await_input": "await_input"},
    )
    graph.add_edge("validate", "respond")
    graph.add_edge("await_input", "respond")   # continues after interrupt is resumed
    graph.add_edge("resume", "diagnose")
    graph.add_edge("respond", END)

    interrupts = ["await_input"] if checkpointer else []
    return graph.compile(checkpointer=checkpointer, interrupt_before=interrupts)
