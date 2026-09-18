"""Thin LiteLLM chat client.

Just enough of the OpenAI chat-completions contract to drive tool selection and
response synthesis. LiteLLM is OpenAI-compatible, so the same payload shape
reaches whichever local model is bound to the `assistant` alias.
"""

from __future__ import annotations

import re
import time
from typing import Any

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

def _strip_inline_reasoning(text: str) -> str:
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

    # Fallback: no closing tag and not already complete JSON means max_tokens
    # cut off before the answer. Take the last sentence-ending line as the
    # closest thing to a response.
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines:
        return lines[-1]
    return text


def trim_for_synthesis(value: Any) -> Any:
    """Strip noise fields from a tool result before it goes into a synthesis
    prompt: opaque IDs the model has no reason to mention, and empty-string
    fields that just add tokens without adding information.

    Confirmed live, twice: calendar events include a `uid` field the model
    has no use for in a spoken/text answer, but its mere presence in the raw
    JSON repeatedly provoked the model into deliberating out loud about
    whether to mention it ("But the user might not need the UIDs or other
    details...") instead of just answering — burning the tight synthesis
    token budget on that instead of a real response, and truncating before
    it got there.
    """
    if isinstance(value, list):
        return [trim_for_synthesis(v) for v in value]
    if isinstance(value, dict):
        return {
            k: trim_for_synthesis(v)
            for k, v in value.items()
            if k != "uid" and v != ""
        }
    return value


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
            content = _strip_inline_reasoning(content)
            msg = {**msg, "content": content}
        return msg
