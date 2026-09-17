"""Response synthesis and step planning node factories."""

from __future__ import annotations

import json
import re
from typing import Any, Callable

import httpx
import structlog

from agent.core.llm import LLMClient

log = structlog.get_logger(__name__)

_SYNTHESIS_SYSTEM = (
    "You are Jarvis, a home assistant. Answer the user from the tool results provided. "
    "Be direct and specific; never describe the tool or the mechanics of the call."
)
_VOICE_HINT = " Your answer is spoken aloud: one or two short sentences, no lists or markup."

# Detects partial reasoning extracted as a "last line" by LLMClient's inline-
# reasoning stripper (agent.core.llm._strip_inline_reasoning) when qwen3's
# thinking runs past max_tokens with no closing </think>. Mirrors the same
# guard in agent.api.routes — see that module for the full incident history:
# without think:false + this rejection, a multi-tool "assistant" (qwen3:30b)
# reply can come back as a truncated mid-thought sentence (e.g. "But the
# user might not need the UIDs...") instead of a real answer, and this graph
# had no check to catch it before returning it straight to the user.
_REASONING_FRAGMENT = re.compile(
    r"^(But (wait|note|remember)|Wait[,.]|Hmm[,.]|Let me (think|check|re|reconsider)|"
    r"I need to|We need to|Actually,|Hold on)",
    re.IGNORECASE,
)

_PLANNING_SYSTEM = (
    "You are a planning assistant. Given a user request and available tools, output a JSON "
    "array of steps to fulfill the request. Each step must be an object with:\n"
    '  "description": str   — what this step accomplishes\n'
    '  "tool": str | null   — qualified tool name to call, or null for an LLM-only step\n'
    '  "args": object       — arguments for the tool (empty object if no tool)\n'
    "Keep steps minimal and concrete. Output only the JSON array, no markdown."
)


def make_synthesizer(llm: LLMClient, speech: bool = False) -> Callable[[dict[str, Any]], Any]:
    """Return an async callable that synthesizes a final natural-language response."""
    system = _SYNTHESIS_SYSTEM + (_VOICE_HINT if speech else "")

    async def synthesize(state: dict[str, Any]) -> dict[str, Any]:
        calls: list[dict[str, Any]] = state.get("tool_calls") or []
        message: str = state.get("message", "")
        history: list[dict[str, Any]] = state.get("messages") or []

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            *history,
            {"role": "user", "content": message},
        ]

        # Inject the last three tool results as context for the LLM
        for call in calls[-3:]:
            if call.get("error"):
                messages.append({
                    "role": "assistant",
                    "content": f"Tool {call.get('tool')} failed: {call['error']}",
                })
            elif call.get("result") is not None:
                messages.append({
                    "role": "assistant",
                    "content": (
                        f"Tool {call.get('tool')} returned: "
                        f"{json.dumps(call['result'], default=str)}"
                    ),
                })

        last = calls[-1] if calls else None
        confidence = 0.7
        if last:
            confidence = 0.4 if last.get("error") else 0.9

        try:
            reply = await llm.complete(
                messages,
                max_tokens=llm.settings.synthesis_max_tokens,
                extra_body={"think": False},
            )
            text = (reply.get("content") or "").strip()
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.warning("synthesis_failed", error=str(exc))
            text = ""

        # Reject text that looks like truncated inline reasoning rather than a
        # real answer: no sentence-ending punctuation, or starts with a
        # reasoning keyword (artifact of the inline-reasoning stripper's
        # last-line fallback). See agent.api.routes for the matching guard.
        if text and not (text[-1] in ".!?" and not _REASONING_FRAGMENT.match(text)):
            log.warning("synthesis_fragment", text=text[:80])
            text = ""

        if not text:
            text = (
                "Done."
                if (last and not last.get("error"))
                else "I couldn't complete that — please try again."
            )
            confidence = min(confidence, 0.3)

        return {"response": text, "confidence": confidence}

    return synthesize


def make_planner(llm: LLMClient) -> Callable[[dict[str, Any]], Any]:
    """Return an async callable that decomposes the user request into a step plan."""

    async def plan(state: dict[str, Any]) -> list[dict[str, Any]]:
        message = state.get("message", "")
        available: list[Any] = state.get("available_tools") or []
        tools_needed: list[str] = state.get("tools_needed") or []
        history: list[dict[str, Any]] = state.get("messages") or []

        tool_names = [
            t if isinstance(t, str) else getattr(t, "name", str(t)) for t in available
        ]
        tool_ctx = f"\nAvailable tools: {', '.join(tool_names[:20])}" if tool_names else ""

        user_msg = (
            f"Request: {message}\n"
            f"Tool categories needed: {', '.join(tools_needed) or 'any'}\n"
            "Output a JSON array of steps."
        )

        try:
            reply = await llm.complete(
                [
                    {"role": "system", "content": _PLANNING_SYSTEM + tool_ctx},
                    *history,
                    {"role": "user", "content": user_msg},
                ]
            )
            content = (reply.get("content") or "").strip()
            # Strip markdown fences if the model wrapped the JSON
            if content.startswith("```"):
                lines = [l for l in content.splitlines() if not l.startswith("```")]
                content = "\n".join(lines).strip()
            steps: list[dict[str, Any]] = json.loads(content)
            if not isinstance(steps, list):
                raise ValueError("planner returned non-list")
        except (json.JSONDecodeError, ValueError, httpx.HTTPError, KeyError) as exc:
            log.warning("planning_failed", error=str(exc))
            # Fall back to a single direct-answer step
            steps = [{"description": message, "tool": None, "args": {}}]

        return steps

    return plan
