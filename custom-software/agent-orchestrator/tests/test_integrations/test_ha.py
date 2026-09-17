"""HA conversation endpoint: speaker-lookup -> scope promotion."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from agent.api.schemas import ChatResponse
from agent.integrations import ha as ha_integration
from agent.integrations.ha import _ConversationInput, ha_conversation


class _FakeRequest:
    def __init__(self, token: str = "Bearer test-token"):
        self.headers = {"Authorization": token} if token else {}


def _payload(text: str = "hello") -> _ConversationInput:
    return _ConversationInput(text=text, language="en", conversation_id="conv-1", agent_id="agent-1")


@pytest.fixture
def captured_chat_req(monkeypatch):
    captured: list = []

    async def fake_chat(chat_req):
        captured.append(chat_req)
        return ChatResponse(message="ok", thread_id="conv-1", output_channel="voice")

    monkeypatch.setattr("agent.api.routes.chat", fake_chat)
    return captured


@pytest.mark.asyncio
async def test_confident_match_promotes_to_personal_scope(monkeypatch, captured_chat_req):
    monkeypatch.setattr(
        ha_integration, "_lookup_speaker", AsyncMock(return_value=("michael", 0.93))
    )

    await ha_conversation(_payload(), _FakeRequest())

    req = captured_chat_req[0]
    assert req.scope == "personal"
    assert req.user_id == "michael"
    assert req.speaker_id == "michael"
    assert req.metadata["speaker_confidence"] == 0.93


@pytest.mark.asyncio
async def test_no_match_falls_back_to_family_scope(monkeypatch, captured_chat_req):
    monkeypatch.setattr(ha_integration, "_lookup_speaker", AsyncMock(return_value=(None, None)))

    await ha_conversation(_payload(), _FakeRequest())

    req = captured_chat_req[0]
    assert req.scope == "family"
    assert req.user_id == "ha_user"
    assert req.speaker_id is None


@pytest.mark.asyncio
async def test_proxy_unreachable_fails_open_to_family_scope(monkeypatch):
    """_lookup_speaker itself must never raise -- verify the real implementation,
    not just that callers handle a mocked failure, since this runs on every
    voice request and must never block the reply.
    """

    class _RaisingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _RaisingClient())

    user_id, confidence = await ha_integration._lookup_speaker()

    assert user_id is None
    assert confidence is None


@pytest.mark.asyncio
async def test_lookup_disabled_when_proxy_url_unset(monkeypatch):
    settings = ha_integration.get_settings()
    monkeypatch.setattr(settings, "wyoming_identify_proxy_url", None)
    monkeypatch.setattr(ha_integration, "get_settings", lambda: settings)

    user_id, confidence = await ha_integration._lookup_speaker()

    assert user_id is None
    assert confidence is None


@pytest.mark.asyncio
async def test_missing_bearer_token_rejected():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        await ha_conversation(_payload(), _FakeRequest(token=""))
    assert excinfo.value.status_code == 401
