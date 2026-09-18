"""Thin LiteLLM chat client.

Just enough of the OpenAI chat-completions contract to drive tool selection and
response synthesis. LiteLLM is OpenAI-compatible, so the same payload shape
reaches whichever local model is bound to the `assistant` alias.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable

import httpx
import structlog
from prometheus_client import Histogram

_llm_latency = Histogram(
    "agent_llm_latency_seconds",
    "LLM call latency by model",
    ["model"],
    buckets=[0.5, 1, 2, 5, 10, 20, 30, 60],
)

from agent.config import Settings

# qwen3 outputs <think>...</think> as literal text when Ollama's native thinking
# mode is inactive. Strip them so the rest of the pipeline sees clean content.
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)

# qwen3 also leaks inline reasoning without think tags — multi-line blocks that
# end with a clear final answer. We detect this by looking for a short sentence
# after a block of reasoning (lines starting with reasoning keywords).

# Phrases that are almost certainly mid-reasoning, not valid answers. Used by
# looks_like_reasoning_fragment to catch a last-line fallback that landed on
# leaked reasoning.
_REASONING_FRAGMENT = re.compile(
    r"^(But (wait|note|remember)|Wait[,.]|Hmm[,.]|Let me (think|check|re|reconsider)|"
    r"I need to|We need to|Actually,|Hold on)",
    re.IGNORECASE,
)


def looks_like_reasoning_fragment(text: str) -> bool:
    """True if ``text`` looks like truncated inline reasoning, not an answer.

    The last-line fallback in _strip_inline_reasoning yields a single line, so
    a fragment is either a reasoning-keyword opener or a single line with no
    sentence-ending punctuation (a reply cut off mid-thought, e.g. "...and
    Bear ("). Multi-line text is a real formatted answer (a list, a haiku, a
    table) and is never a fragment — its last line legitimately may not end
    in punctuation.
    """
    if _REASONING_FRAGMENT.match(text):
        return True
    # A closing quote/paren/emphasis mark after the final punctuation is still
    # a complete sentence: 'You have "Soccer practice."' / '(at 10:00 AM.)'
    return "\n" not in text and text.rstrip("\"')]*_”’")[-1:] not in (".", "!", "?")


def _strip_inline_reasoning(text: str, truncated: bool = True) -> str:
    """Extract the answer from qwen3's combined reasoning+answer output.

    qwen3 with think:false outputs reasoning as plain text and ends the thinking
    block with </think>, then gives the actual answer. We cut everything up to
    and including </think> and return only the clean final answer.

    If </think> is absent, the model *usually* reasoned inline without tags
    (budget was too tight to finish) — but grammar-constrained calls (tool
    selection, routing) can legitimately return a complete, valid JSON object
    formatted across multiple lines with no thinking at all. Blindly taking
    "the last line" in that case shreds valid multi-line JSON down to a
    trailing brace — confirmed live: a calendar tool-selection call returned
    the complete, valid two-line
    '{"tool": "mcp-calendar__calendar_events", ...}\n}', and this fallback
    turned it into just '}', which then failed to parse and silently dropped
    the tool call. So: only take the last-line fallback when the text isn't
    already a complete top-level JSON object/array.

    ``truncated`` says whether generation was cut off (finish_reason other than
    "stop"). Models that don't leak reasoning (e.g. qwen3.8 with think:false)
    return a clean multi-line answer with no </think> at all; taking the last
    line of that shreds lists, haiku and tables. Only a truncated reply gets
    the last-line fallback; a cleanly finished one is returned whole.
    """
    if not text:
        return text

    # Primary case: qwen3 closed its thinking block — take the answer after it.
    if "</think>" in text:
        after = text.split("</think>", 1)[1].strip()
        return after if after else text

    stripped = text.strip()
    if stripped[:1] in "{[" and stripped[-1:] in "}]":
        return stripped

    if not truncated:
        return stripped

    # Fallback: no closing tag, not complete JSON, and generation was cut off
    # before the answer. Take the last line as the closest thing to a response.
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines:
        return lines[-1]
    return text


def filter_recursive(value: Any, keep: Callable[[str, Any], bool]) -> Any:
    """Rebuild a JSON-like value, keeping only dict entries for which
    ``keep(key, value)`` is true. Recurses into lists and nested dicts at
    any depth; non-dict/list values pass through unchanged.

    Shared core for trim_for_synthesis's denylist and
    agent.mcp.synthesis_projections' per-tool allowlists — same walk, only
    the predicate differs.
    """
    if isinstance(value, list):
        return [filter_recursive(v, keep) for v in value]
    if isinstance(value, dict):
        return {k: filter_recursive(v, keep) for k, v in value.items() if keep(k, v)}
    return value


def trim_for_synthesis(value: Any) -> Any:
    """Strip noise fields from a tool result before it goes into a synthesis
    prompt: opaque IDs the model has no reason to mention, and empty-string
    fields that just add tokens without adding information.

    This is the generic fallback for any tool without an explicit allowlist
    in agent.mcp.synthesis_projections — a denylist can only ever catch the
    noise patterns we've actually seen (uid, empty strings), not whatever a
    new tool introduces. Confirmed live, twice: calendar events include a
    `uid` field the model has no use for in a spoken/text answer, but its
    mere presence in the raw JSON repeatedly provoked the model into
    deliberating out loud about whether to mention it ("But the user might
    not need the UIDs or other details...") instead of just answering —
    burning the tight synthesis token budget on that instead of a real
    response, and truncating before it got there.
    """
    return filter_recursive(value, keep=lambda k, v: k != "uid" and v != "")


log = structlog.get_logger(__name__)


class LLMClient:
    """Chat completions against the LiteLLM proxy."""

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.settings = settings
        self._client = client
        self.timeout = timeout

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 800,
        extra_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return the assistant message dict. Raises on transport failure."""
        payload: dict[str, Any] = {
            "model": model or self.settings.litellm_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if extra_body:
            payload["extra_body"] = extra_body

        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        t0 = time.monotonic()
        try:
            response = await client.post(
                f"{self.settings.litellm_base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.settings.litellm_api_key}"},
            )
            response.raise_for_status()
            data = response.json()
        finally:
            if self._client is None:
                await client.aclose()

        elapsed = time.monotonic() - t0
        used_model = model or self.settings.litellm_model
        _llm_latency.labels(model=used_model).observe(elapsed)
        log.debug("llm_call_done", model=used_model, seconds=round(elapsed, 2))

        choices = data.get("choices") or []
        if not choices:
            log.warning("llm_empty_choices", model=used_model, raw=str(data)[:200])
            return {"role": "assistant", "content": ""}

        msg = choices[0]["message"]
        raw_content = msg.get("content") or ""
        if not raw_content:
            finish = choices[0].get("finish_reason", "unknown")
            usage = data.get("usage", {})
            log.warning(
                "llm_empty_content",
                model=used_model,
                finish_reason=finish,
                completion_tokens=usage.get("completion_tokens"),
                max_tokens=max_tokens,
            )
        if raw_content:
            content = _THINK_RE.sub("", raw_content).strip()
            content = _strip_inline_reasoning(
                content, truncated=choices[0].get("finish_reason") != "stop"
            )
            msg = {**msg, "content": content}
        return msg
