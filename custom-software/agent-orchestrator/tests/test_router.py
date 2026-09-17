"""Meta-router: JSON parsing, tool cap, and graceful fallback."""

import json

import httpx
import pytest

from agent.config import Settings
from agent.core.router import FALLBACK, TOOL_CATEGORIES, MetaRouter


def make_router(handler) -> MetaRouter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://litellm:4000")
    return MetaRouter(Settings(), client=client)


def completion(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


@pytest.mark.asyncio
async def test_parses_clean_json():
    body = ('{"intent":"command","graph":"simple","tools_needed":["home_assistant"],'
            '"execution_mode":"sync","output_channel":"voice","parallel_steps":false}')
    router = make_router(lambda req: completion(body))
    # "please turn off..." rather than a bare imperative: the latter now
    # matches _heuristic's _COMMAND_RE fast-path and never reaches this
    # mocked LLM response at all — this test is specifically about JSON
    # parsing, so it needs a message that actually goes to the LLM.
    decision = await router.route("please turn off the lights", {"source": "voice"})
    assert decision.intent == "command"
    assert decision.graph == "simple"
    assert decision.tools_needed == ["home_assistant"]


@pytest.mark.asyncio
async def test_strips_markdown_fences():
    body = ('```json\n{"intent":"question","graph":"simple","tools_needed":[],'
            '"execution_mode":"sync","output_channel":"webui","parallel_steps":false}\n```')
    router = make_router(lambda req: completion(body))
    # Pre-existing gap, unrelated to the command heuristic added alongside
    # this fix: "what time is it" matches _SIMPLE_RE's "what time" prefix,
    # so it was already short-circuiting to the heuristic FALLBACK and
    # never reaching this mocked LLM response — silently not testing the
    # fence-stripping this test is named for. Use a question _SIMPLE_RE
    # doesn't recognize instead.
    decision = await router.route("when does the game start", {"source": "webui"})
    assert decision.intent == "question"


@pytest.mark.asyncio
async def test_tools_capped_at_seven_and_unknowns_dropped():
    tools = ["home_assistant", "memory", "calendar", "search", "documents",
             "notifications", "browser", "filesystem", "bogus"]
    body = ('{"intent":"research","graph":"research","tools_needed":%s,'
            '"execution_mode":"async","output_channel":"push","parallel_steps":true}' % str(tools).replace("'", '"'))
    router = make_router(lambda req: completion(body))
    decision = await router.route("research something", {"source": "webui"})
    assert len(decision.tools_needed) == 7
    assert "bogus" not in decision.tools_needed


@pytest.mark.asyncio
async def test_llm_call_disables_thinking_and_constrains_to_schema():
    """think:true never terminates on this classification task (verified
    against the live model, empty content up to a 1500-token budget) and
    temperature 0 reproducibly made qwen3:4b stop instantly with zero
    tokens — so both must be set correctly on every outgoing request, not
    just left as the model's configured defaults.
    """
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        body = ('{"intent":"question","graph":"simple","tools_needed":[],'
                '"execution_mode":"sync","output_channel":"webui","parallel_steps":false}')
        return completion(body)

    router = make_router(handler)
    await router.route("when does the game start", {"source": "webui"})

    assert captured["temperature"] == 0.2
    assert captured["extra_body"]["think"] is False
    schema = captured["extra_body"]["format"]
    assert schema["properties"]["tools_needed"]["items"]["enum"] == TOOL_CATEGORIES
    assert set(schema["required"]) == set(schema["properties"])


@pytest.mark.asyncio
async def test_short_question_reaches_llm_for_correct_tool_category():
    """Regression: a blanket "short question -> FALLBACK" heuristic used to
    short-circuit every brief '?' message to tools_needed=["memory"],
    so a calendar question never reached the LLM classifier and mcp-calendar
    was never selected. Short questions must now reach the LLM so it can
    pick the right category.
    """
    body = ('{"intent":"question","graph":"simple","tools_needed":["calendar"],'
            '"execution_mode":"sync","output_channel":"webui","parallel_steps":false}')
    router = make_router(lambda req: completion(body))
    decision = await router.route("What's on my calendar today?", {"source": "webui"})
    assert decision.tools_needed == ["calendar"]


@pytest.mark.asyncio
async def test_falls_back_on_malformed_json():
    router = make_router(lambda req: completion("I think you want the lights off!"))
    decision = await router.route("lights", {"source": "webui"})
    assert decision.graph == FALLBACK.graph
    assert decision.intent == FALLBACK.intent


@pytest.mark.asyncio
async def test_falls_back_on_http_error():
    router = make_router(lambda req: httpx.Response(500))
    decision = await router.route("anything", {"source": "webui"})
    assert decision.graph == FALLBACK.graph


@pytest.mark.asyncio
async def test_voice_source_forces_voice_output_when_sync():
    body = ('{"intent":"command","graph":"simple","tools_needed":[],'
            '"execution_mode":"sync","output_channel":"webui","parallel_steps":false}')
    router = make_router(lambda req: completion(body))
    decision = await router.route("lights off", {"source": "voice"})
    assert decision.output_channel == "voice"


@pytest.mark.asyncio
async def test_async_keeps_push_channel_even_from_voice():
    body = ('{"intent":"research","graph":"research","tools_needed":["search"],'
            '"execution_mode":"async","output_channel":"push","parallel_steps":false}')
    router = make_router(lambda req: completion(body))
    decision = await router.route("deep research", {"source": "voice"})
    assert decision.output_channel == "push"


def _fail_if_called(request):
    raise AssertionError("router hit the LLM — heuristic should have short-circuited")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "turn off the kitchen lights",
        "turn on the living room lamp",
        "lock the front door",
        "unlock the back door",
        "set the thermostat to 68",
        "dim the bedroom lights",
        "play some music",
        "pause the music",
        "stop the timer",
    ],
)
async def test_command_heuristic_skips_llm_for_home_assistant_actuations(message):
    router = make_router(_fail_if_called)
    decision = await router.route(message, {"source": "voice"})
    assert decision.intent == "command"
    assert decision.tools_needed == ["home_assistant"]
    assert decision.graph == "simple"
    assert decision.output_channel == "voice"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "please turn off the lights",  # polite phrasing isn't a bare imperative
        "stop bothering me",  # bare "stop" without a media/timer object
        "can you turn the lights off",  # verb isn't at the start
    ],
)
async def test_command_heuristic_does_not_overreach(message):
    """Phrasing outside the narrow imperative shape still goes to the LLM."""
    body = ('{"intent":"command","graph":"simple","tools_needed":["home_assistant"],'
            '"execution_mode":"sync","output_channel":"webui","parallel_steps":false}')
    router = make_router(lambda req: completion(body))
    decision = await router.route(message, {"source": "webui"})
    assert decision.intent == "command"
