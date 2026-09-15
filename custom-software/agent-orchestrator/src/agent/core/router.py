"""Meta-reasoning router — fast intent classification before the main graph."""

from __future__ import annotations

import json
import re

import httpx
import structlog
from pydantic import ValidationError

from agent.api.schemas import RoutingDecision
from agent.config import Settings

# Patterns that indicate the user needs async research or a multi-step plan.
_RESEARCH_RE = re.compile(
    r"\b(research|investigate|find out|look into|summarize|compare|analyze|report)\b",
    re.IGNORECASE,
)
# Simple greetings and quick questions that never need the LLM router.
_SIMPLE_RE = re.compile(
    r"^(hi|hello|hey|thanks|thank you|what time|what('s| is) the time|"
    r"what('s| is) today|what day|good (morning|evening|afternoon|night))\b",
    re.IGNORECASE,
)
# Single home_assistant actuations phrased as an imperative at the start of
# the message — the natural shape of a voice command ("turn off the kitchen
# lights", "lock the front door"). Deliberately narrow: matched only at the
# start, so a command verb mentioned mid-sentence in ordinary conversation
# doesn't false-positive into skipping real classification.
_COMMAND_RE = re.compile(
    r"^(turn (on|off)|switch (on|off)|(dim|brighten) the|"
    r"set (the )?(temperature|thermostat|brightness)|"
    r"(lock|unlock) the|(open|close) the|"
    r"(start|stop) the|"
    # These verbs are common English outside a command context ("stop
    # worrying"), so require a media/timer object — never bare.
    r"(play|pause|resume|stop|skip) (the |some )?(music|song|playlist|track|timer))\b",
    re.IGNORECASE,
)

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

# No JSON-formatting instructions here on purpose: grammar-constrained
# decoding (_ROUTING_SCHEMA below) guarantees valid JSON at the sampling
# level, so the prompt only needs to carry classification judgment, not
# output-format boilerplate the model would otherwise have to "think"
# about producing correctly.
SYSTEM_PROMPT = """Classify the user's message for a home assistant.

- command: a single actuation ("turn off the lights") -> graph simple, sync
- question: a lookup answerable in one or two tool calls -> graph simple, sync
- research: open-ended investigation -> graph research, async, output push
- diagnostic: needs back-and-forth with the user -> graph interactive, sync
- conversation: chit-chat or memory recall, often no tools -> graph simple, sync

tools_needed is a subset of {categories}, max 7. Requests arriving from voice \
should answer via voice unless async."""

# Deterministic fallback when the router model is unavailable or misbehaves.
FALLBACK = RoutingDecision(
    intent="question",
    graph="simple",
    tools_needed=["memory"],
    execution_mode="sync",
    output_channel="webui",
    parallel_steps=False,
)


def _routing_schema() -> dict:
    """JSON schema for Ollama's grammar-constrained decoding, built from
    RoutingDecision itself so the two can't drift.

    One patch is needed: pydantic can't infer an enum for tools_needed
    (it's a plain list[str] — validated post-hoc against TOOL_CATEGORIES in
    _parse), but grammar constraint needs it spelled out up front. Verified
    directly against the live model: every already-enum-constrained field
    (intent, graph, execution_mode, output_channel) came back clean, while
    this one unconstrained free-string field was exactly where garbled
    characters leaked in under think:false — see router latency
    investigation, 2026-09-15.
    """
    schema = RoutingDecision.model_json_schema()
    schema["properties"]["tools_needed"]["items"] = {
        "type": "string",
        "enum": TOOL_CATEGORIES,
    }
    # Require every field so the model always emits a complete object —
    # pydantic's optional-with-default distinction doesn't matter here.
    schema["required"] = list(schema["properties"].keys())
    return schema


_ROUTING_SCHEMA = _routing_schema()


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

    def _heuristic(self, message: str) -> RoutingDecision | None:
        """Return a decision instantly for obvious cases, bypassing the LLM.

        Saves a full router-classification round trip for greetings, simple
        questions, and single home_assistant actuations. Research-flavored
        phrasing always goes to the LLM.
        """
        if _RESEARCH_RE.search(message):
            return None
        if _SIMPLE_RE.match(message.strip()):
            # Greetings and time queries need no tools — skip tool selection entirely.
            return FALLBACK.model_copy(update={"intent": "conversation", "tools_needed": []})
        if _COMMAND_RE.match(message.strip()):
            return FALLBACK.model_copy(
                update={"intent": "command", "tools_needed": ["home_assistant"]}
            )
        if len(message) < 60 and "?" in message and not _RESEARCH_RE.search(message):
            return FALLBACK.model_copy()
        return None

    async def route(self, message: str, user_context: dict) -> RoutingDecision:
        """Classify a message into intent, graph, tools, and execution strategy.

        Never raises: on any failure this returns the conservative FALLBACK so a
        router outage degrades to a simple sync answer rather than a 500.
        """
        fast = self._heuristic(message)
        if fast is not None:
            log.debug("router_heuristic", message=message[:60])
            if user_context.get("source") == "voice" and fast.execution_mode == "sync":
                fast.output_channel = "voice"
            return fast

        payload = {
            "model": self.settings.router_model,
            # temperature 0 reproducibly made qwen3:4b emit an immediate
            # stop token (0 completion tokens) on this classification
            # prompt — verified directly against the live model. 0.2 is
            # what every working sample used; see router latency
            # investigation, 2026-09-15.
            "temperature": 0.2,
            "max_tokens": self.settings.router_max_tokens,
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
            # think:true (the model's configured default) never terminates
            # on this classification task — verified up to a 1500-token
            # budget, always empty content. Grammar-constrained decoding
            # via `format` doesn't need "thinking" to reach valid output,
            # and disabling it is what makes this call fast (~3-4s) instead
            # of either hanging or falling through to FALLBACK every time.
            "extra_body": {"think": False, "format": _ROUTING_SCHEMA},
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
