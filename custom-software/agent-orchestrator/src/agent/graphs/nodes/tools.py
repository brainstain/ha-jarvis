"""Tool selection and execution node factories."""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import structlog

from agent.core.llm import LLMClient
from agent.core.safety import SafetyGuard
from agent.mcp.registry import MCPToolRegistry, render_openai_tools
from agent.mcp.tool_filter import ToolFilter

log = structlog.get_logger(__name__)

_TOOL_SELECTION_SYSTEM = (
    "Pick the single tool that answers the user's request, "
    "or answer directly if no tool fits."
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

        messages = [
            {"role": "system", "content": _TOOL_SELECTION_SYSTEM + mem_ctx},
            {"role": "user", "content": state.get("message", "")},
        ]

        try:
            msg = await llm.complete(messages, tools=render_openai_tools(usable))
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.warning("tool_selection_failed", error=str(exc))
            return {}

        calls = msg.get("tool_calls") or []
        if not calls:
            return {}

        fn = calls[0].get("function", {})
        name = fn.get("name", "")
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        if not isinstance(args, dict):
            args = {}

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
