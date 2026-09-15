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

def _strip_inline_reasoning(text: str) -> str:
    """Extract the answer from qwen3's combined reasoning+answer output.

    qwen3 with think:false outputs reasoning as plain text and ends the thinking
    block with </think>, then gives the actual answer. We cut everything up to
    and including </think> and return only the clean final answer.

    If </think> is absent, the model reasoned inline without tags (budget was
    too tight to finish). We fall back to the last non-empty line as a best
    approximation of the intended answer.
    """
    if not text:
        return text

    # Primary case: qwen3 closed its thinking block — take the answer after it.
    if "</think>" in text:
        after = text.split("</think>", 1)[1].strip()
        return after if after else text

    # Fallback: no closing tag means max_tokens cut off before the answer.
    # Take the last sentence-ending line as the closest thing to a response.
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines:
        return lines[-1]
    return text

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
