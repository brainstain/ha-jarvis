"""Meta-reasoning router — fast intent classification before the main graph."""

from __future__ import annotations

import json

import httpx
import structlog
from pydantic import ValidationError

from agent.api.schemas import RoutingDecision
from agent.config import Settings

log = structlog.get_logger(__name__)

TOOL_CATEGORIES = [
    "home_assistant",
    "memory",
    "calendar",
    "search",
    "documents",
    "notifications",
    "browser",
    "filesystem",
]

SYSTEM_PROMPT = """You are a routing classifier. Given the user message and context, \
determine intent, required tool categories, and execution strategy. Respond in JSON only, \
with no preamble and no markdown fences.

Schema:
{{
  "intent": "command|question|research|diagnostic|conversation",
  "graph": "simple|multistep|research|interactive",
  "tools_needed": [subset of {categories}, max 7],
  "execution_mode": "sync|async",
  "output_channel": "voice|webui|push",
  "parallel_steps": true|false
}}

Guidance:
- command: a single actuation ("turn off the lights") -> graph simple, sync
- question: a lookup answerable in one or two tool calls -> graph simple, sync
- research: open-ended investigation -> graph research, async, output push
- diagnostic: needs back-and-forth with the user -> graph interactive, sync
- conversation: chit-chat or memory recall, often no tools -> graph simple, sync
- Requests arriving from voice should answer via voice unless async."""

# Deterministic fallback when the router model is unavailable or misbehaves.
FALLBACK = RoutingDecision(
    intent="question",
    graph="simple",
    tools_needed=["memory"],
    execution_mode="sync",
    output_channel="webui",
    parallel_steps=False,
)


class MetaRouter:
    """Fast classification using the assistant-fast model via LiteLLM.

    Runs before the main agent graph so that full tool schemas never enter the
    large model's context on simple requests.
    """

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client

    async def _post(self, payload: dict) -> dict:
        client = self._client or httpx.AsyncClient(timeout=15.0)
        try:
            response = await client.post(
                f"{self.settings.litellm_base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.settings.litellm_api_key}"},
            )
            response.raise_for_status()
            return response.json()
        finally:
            if self._client is None:
                await client.aclose()

    @staticmethod
    def _parse(content: str) -> RoutingDecision:
        """Parse model output into a RoutingDecision, tolerating stray fences."""
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            cleaned = cleaned.removeprefix("json").strip()
        data = json.loads(cleaned)
        # Enforce the max-7 tool rule before validation.
        if isinstance(data.get("tools_needed"), list):
            data["tools_needed"] = [t for t in data["tools_needed"] if t in TOOL_CATEGORIES][:7]
        return RoutingDecision.model_validate(data)

    async def route(self, message: str, user_context: dict) -> RoutingDecision:
        """Classify a message into intent, graph, tools, and execution strategy.

        Never raises: on any failure this returns the conservative FALLBACK so a
        router outage degrades to a simple sync answer rather than a 500.
        """
        payload = {
            "model": self.settings.router_model,
            "temperature": 0,
            "max_tokens": 300,
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT.format(categories=TOOL_CATEGORIES),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"message": message, "context": user_context}, default=str
                    ),
                },
            ],
        }

        try:
            data = await self._post(payload)
            content = data["choices"][0]["message"]["content"]
            decision = self._parse(content)
        except (httpx.HTTPError, KeyError, json.JSONDecodeError, ValidationError) as exc:
            log.warning("router_fallback", error=str(exc), message=message[:80])
            decision = FALLBACK.model_copy()

        # Voice requests answer by voice unless the work is async.
        if user_context.get("source") == "voice" and decision.execution_mode == "sync":
            decision.output_channel = "voice"
        return decision
