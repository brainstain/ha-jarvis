"""Shared LangGraph state for all agent workflows."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict


def _append(left: list, right: list) -> list:
    """Reducer: concatenate list updates from parallel branches."""
    return (left or []) + (right or [])


class AgentState(TypedDict, total=False):
    """State threaded through every graph node.

    `messages`, `tool_calls`, `tools_used`, and `memory_updates` use an append
    reducer so parallel fan-out (Send API) merges cleanly instead of clobbering.
    """

    # Request context
    message: str
    user_id: str
    scope: str
    thread_id: str
    source: str
    output_channel: str

    # Conversation
    messages: Annotated[list[dict[str, Any]], _append]
    memories: list[dict[str, Any]]

    # Planning / execution
    plan: list[dict[str, Any]]
    current_step: int
    tool_calls: Annotated[list[dict[str, Any]], _append]
    tools_used: Annotated[list[str], _append]
    memory_updates: Annotated[list[dict[str, Any]], _append]
    available_tools: list[dict[str, Any]]

    # Safety accounting
    iteration_count: int
    tokens_used: int
    compressed: bool
    halted_reason: str

    # HITL
    pending_question: str
    user_response: str

    # Output
    response: str
    confidence: float
