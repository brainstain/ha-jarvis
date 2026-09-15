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

from agent.config import Settings

# qwen3 outputs <think>...</think> as literal text when Ollama's native thinking
# mode is inactive. Strip them so the rest of the pipeline sees clean content.
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)

# qwen3 also leaks inline reasoning without think tags — multi-line blocks that
# end with a clear final answer. We detect this by looking for a short sentence
# after a block of reasoning (lines starting with reasoning keywords).
_INLINE_REASONING_PREFIXES = (
    "we are ", "i should ", "let me ", "the user ", "let's ", "since ", "now,",
    "we need", "we can", "we have", "i need", "so we", "so i", "therefore",
    "actually,", "however,", "wait,", "note:", "step ", "first,", "finally,",
    "the tool", "we don't", "we want",
)


def _strip_inline_reasoning(text: str) -> str:
    """Remove qwen3's visible reasoning prefix, keeping only the final answer.

    qwen3 with think:false sometimes outputs multi-line chain-of-thought text
    before giving the actual answer. We detect this by splitting on blank lines
    and discarding leading paragraphs that look like reasoning.
    """
    if not text:
        return text

    # Split into blank-line-separated blocks.
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if len(blocks) <= 1:
        # Single paragraph — check if it's pure reasoning and trim to last sentence.
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(lines) > 3 and all(
            ln.lower().startswith(_INLINE_REASONING_PREFIXES) for ln in lines[:-1]
        ):
            return lines[-1]
        return text

    # Find first block that doesn't look like reasoning.
    for block in blocks:
        first_line = block.splitlines()[0].lower().strip()
        if not first_line.startswith(_INLINE_REASONING_PREFIXES):
            return block

    # All blocks looked like reasoning — fall back to last non-empty block.
    return blocks[-1]

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
        log.debug("llm_call_done", model=used_model, seconds=round(elapsed, 2))

        msg = data["choices"][0]["message"]
        if msg.get("content"):
            content = _THINK_RE.sub("", msg["content"]).strip()
            content = _strip_inline_reasoning(content)
            msg = {**msg, "content": content}
        return msg
