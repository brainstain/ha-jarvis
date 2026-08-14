"""Meta-router: JSON parsing, tool cap, and graceful fallback."""

import httpx
import pytest

from agent.config import Settings
from agent.core.router import FALLBACK, MetaRouter


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
    decision = await router.route("turn off the lights", {"source": "voice"})
    assert decision.intent == "command"
    assert decision.graph == "simple"
    assert decision.tools_needed == ["home_assistant"]


@pytest.mark.asyncio
async def test_strips_markdown_fences():
    body = ('```json\n{"intent":"question","graph":"simple","tools_needed":[],'
            '"execution_mode":"sync","output_channel":"webui","parallel_steps":false}\n```')
    router = make_router(lambda req: completion(body))
    decision = await router.route("what time is it", {"source": "webui"})
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
