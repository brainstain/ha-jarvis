"""Thin LiteLLM chat client.

Just enough of the OpenAI chat-completions contract to drive tool selection and
response synthesis. LiteLLM is OpenAI-compatible, so the same payload shape
reaches whichever local model is bound to the `assistant` alias.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from agent.config import Settings

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

        return data["choices"][0]["message"]
