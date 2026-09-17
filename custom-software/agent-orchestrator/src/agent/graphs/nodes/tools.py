"""Tool selection and execution node factories."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable

import httpx
import structlog

from agent.core.llm import LLMClient
from agent.core.safety import SafetyGuard
from agent.mcp.registry import (
    MCPToolRegistry,
    inject_identity_args,
    parse_tool_selection,
    render_tool_descriptions,
    render_tool_selection_schema,
)
from agent.mcp.tool_filter import ToolFilter

log = structlog.get_logger(__name__)


def _tool_selection_system() -> str:
    """Build the tool-selection system prompt with the actual current date.

    Without this, a tool whose docstring includes an example date (e.g.
    calendar_events' "start: ... e.g. '2025-01-20T00:00:00'") gets that
    example copied into the model's output verbatim for relative queries
    like "today" — confirmed live: every "what's on my calendar today"
    query queried 2025-01-20 instead of the real date, silently returning
    whatever (if anything) happened to be on that literal day.
    """
    now = datetime.now(UTC)
    return (
        "Pick the single tool that answers the user's request, "
        'or "none" if no tool fits. '
        f"The current date and time is {now.strftime('%Y-%m-%d %H:%M')} UTC "
        f"({now.strftime('%A')}) — use this, not any example date in a tool's "
        "description, to compute relative dates like \"today\" or \"tomorrow\"."
    )


def make_tool_executor(
    llm: LLMClient,
    mcp: MCPToolRegistry,
    tool_filter: ToolFilter,
    guard: SafetyGuard,
    categories: list[str] | None = None,
    max_tools: int = 7,
) -> Callable[[dict[str, Any]], Any]:
    """Return an async callable that selects one tool via the LLM and calls it.

    ``categories`` may be pre-bound (for simple graph) or read from state
    (for multistep, which populates ``tools_needed`` per step).
    """

    async def execute(state: dict[str, Any]) -> dict[str, Any]:
        cats = categories or state.get("tools_needed") or []
        selected = tool_filter.select_tools(cats, max_tools=max_tools)
        usable = [t for t in selected if not guard.check_circuit_breaker(t.name)]
        if not usable or mcp is None:
            log.info("no_usable_tools", categories=cats)
            return {}

        memories = state.get("memories", [])
        mem_ctx = ""
        if memories:
            lines = "\n".join(f"- {m['text']}" for m in memories[:3])
            mem_ctx = f"\nRelevant context from memory:\n{lines}"

        # Grammar-constrained decoding, not native tools=[...] function-calling
        # — see agent/api/routes.py's execute_tool() for why (native
        # tool-calling doesn't reliably produce tool_calls on this stack).
        messages = [
            {
                "role": "system",
                "content": (
                    _tool_selection_system()
                    + mem_ctx
                    + "\n\nAvailable tools:\n"
                    + render_tool_descriptions(usable)
                ),
            },
            {"role": "user", "content": state.get("message", "")},
        ]

        try:
            msg = await llm.complete(
                messages,
                extra_body={"think": False, "format": render_tool_selection_schema(usable)},
            )
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.warning("tool_selection_failed", error=str(exc))
            return {}

        name, args = parse_tool_selection(msg.get("content") or "", usable)
        if name is None:
            return {}
        tool = next(t for t in usable if t.name == name)
        args = inject_identity_args(tool, args, state.get("user_id", ""))

        if guard.check_circuit_breaker(name):
            return {
                "tool_calls": [{"tool": name, "args": args, "error": "circuit_open"}],
                "tools_used": [name],
            }

        envelope = await mcp.call(name, args)
        return {"tool_calls": [{**envelope, "args": args}], "tools_used": [name]}

    return execute


def make_parallel_executor(
    mcp: MCPToolRegistry,
    guard: SafetyGuard,
) -> Callable[[dict[str, Any]], Any]:
    """Return an async callable that executes the *current* planned step.

    The multistep graph increments ``current_step`` after each call; this
    callable reads the step's tool name and args from the plan and calls MCP.
    """

    async def execute_step(state: dict[str, Any]) -> dict[str, Any]:
        plan: list[dict[str, Any]] = state.get("plan") or []
        idx = state.get("current_step", 0)
        if idx >= len(plan):
            return {}

        step = plan[idx]
        tool_name: str = step.get("tool") or ""
        args: dict[str, Any] = step.get("args") or {}

        if not tool_name:
            # LLM synthesis step — no MCP call, just return step description
            return {
                "tool_calls": [
                    {"tool": "llm", "result": step.get("description", ""), "args": {}}
                ]
            }

        if guard.check_circuit_breaker(tool_name):
            return {
                "tool_calls": [{"tool": tool_name, "args": args, "error": "circuit_open"}],
                "tools_used": [tool_name],
            }

        envelope = await mcp.call(tool_name, args)
        return {"tool_calls": [{**envelope, "args": args}], "tools_used": [tool_name]}

    return execute_step
