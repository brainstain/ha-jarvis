"""Tests for the HA Jarvis conversation agent (agent-orchestrator bridge)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.ha_jarvis.conversation import JarvisConversationEntity

from .conftest import make_orchestrator_response


# ---------------------------------------------------------------------------
# Helper: build a properly structured aiohttp mock
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Mimics an aiohttp response."""

    def __init__(self, json_data: dict, status: int = 200):
        self._json_data = json_data
        self.status = status

    async def json(self):
        return self._json_data

    async def text(self):
        return json.dumps(self._json_data)


class _FakeResponseCtx:
    """Async context manager wrapping a _FakeResponse."""

    def __init__(self, response: _FakeResponse):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *args):
        pass


class _FakeSession:
    """Mimics aiohttp.ClientSession with a single configurable response."""

    def __init__(self, response: dict, status: int = 200, capture: list | None = None):
        self._response = response
        self._status = status
        self._capture = capture

    def post(self, url, *, json=None, headers=None, timeout=None):
        if self._capture is not None:
            self._capture.append({"url": url, "json": json, "headers": headers})
        return _FakeResponseCtx(_FakeResponse(self._response, self._status))

    def get(self, url, *, timeout=None):
        return _FakeResponseCtx(_FakeResponse(self._response, self._status))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class _FakeErrorSession:
    """Mimics aiohttp.ClientSession that returns an HTTP error status."""

    def __init__(self, status: int, text: str):
        self._status = status
        self._text = text

    def post(self, url, *, json=None, headers=None, timeout=None):
        resp = _FakeResponse({}, status=self._status)

        async def _text():
            return self._text

        resp.text = _text
        return _FakeResponseCtx(resp)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class _FakeRaisingSession:
    """Mimics aiohttp.ClientSession that raises a connection error."""

    def post(self, url, *, json=None, headers=None, timeout=None):
        raise aiohttp_client_error()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def aiohttp_client_error():
    import aiohttp

    return aiohttp.ClientConnectionError("connection refused")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def entity(mock_hass, mock_config_entry):
    """Create a JarvisConversationEntity with mocked HA dependencies."""
    return JarvisConversationEntity(mock_config_entry, mock_hass)


@pytest.fixture
def patch_post():
    """Provide a helper to patch aiohttp.ClientSession for POST calls."""
    from contextlib import contextmanager

    @contextmanager
    def _patch(response: dict | None = None, *, status: int = 200,
               error_status: int | None = None, error_text: str = "",
               raises: bool = False):
        captured: list[dict] = []
        if raises:
            session = _FakeRaisingSession()
        elif error_status is not None:
            session = _FakeErrorSession(error_status, error_text)
        else:
            session = _FakeSession(response or {}, status=status, capture=captured)

        with patch("aiohttp.ClientSession", return_value=session):
            yield captured

    return _patch


# ===================================================================
# Tests: forwarding to agent-orchestrator (try_ha_first disabled)
# ===================================================================


class TestOrchestratorBridge:
    """Tests for the direct-to-orchestrator path."""

    @pytest.mark.asyncio
    async def test_simple_response(self, entity, mock_user_input, mock_chat_log, patch_post):
        """A plain orchestrator response is spoken back."""
        entity.entry.options["try_ha_first"] = False
        mock_user_input.text = "What's on my calendar today?"

        resp = make_orchestrator_response("You have no events today.")

        with patch_post(resp):
            result = await entity._async_handle_message(mock_user_input, mock_chat_log)

        result.response.async_set_speech.assert_called_once_with(
            "You have no events today."
        )

    @pytest.mark.asyncio
    async def test_conversation_id_is_passed_and_resolved(
        self, entity, mock_user_input, mock_chat_log, patch_post
    ):
        """The request carries conversation_id; the resolved id comes back in the result."""
        mock_user_input.conversation_id = "conv-1"
        entity.entry.options["try_ha_first"] = False

        resp = make_orchestrator_response("Sure thing.", conversation_id="conv-1-resolved")

        with patch_post(resp) as captured:
            result = await entity._async_handle_message(mock_user_input, mock_chat_log)

        assert captured[0]["json"]["conversation_id"] == "conv-1"
        assert result.conversation_id == "conv-1-resolved"

    @pytest.mark.asyncio
    async def test_request_payload_shape(self, entity, mock_user_input, mock_chat_log, patch_post):
        """Verify the POST payload matches the /ha/conversation/process contract."""
        entity.entry.options["try_ha_first"] = False
        mock_user_input.text = "turn on the lights"
        mock_user_input.language = "en"
        mock_user_input.agent_id = "agent-42"

        resp = make_orchestrator_response("Done.")

        with patch_post(resp) as captured:
            await entity._async_handle_message(mock_user_input, mock_chat_log)

        payload = captured[0]["json"]
        assert payload == {
            "text": "turn on the lights",
            "language": "en",
            "conversation_id": "test-conv-123",
            "agent_id": "agent-42",
        }
        assert captured[0]["url"].endswith("/ha/conversation/process")
        assert captured[0]["headers"]["Authorization"] == "Bearer homeassistant"

    @pytest.mark.asyncio
    async def test_api_key_used_as_bearer_token(self, mock_hass, mock_config_entry, mock_user_input, mock_chat_log, patch_post):
        """When an api_key is configured, it's sent as the bearer token."""
        mock_hass.data["ha_jarvis"]["test-entry-id"]["api_key"] = "sk-my-token"
        entity = JarvisConversationEntity(mock_config_entry, mock_hass)
        entity.entry.options["try_ha_first"] = False

        resp = make_orchestrator_response("ok")

        with patch_post(resp) as captured:
            await entity._async_handle_message(mock_user_input, mock_chat_log)

        assert captured[0]["headers"]["Authorization"] == "Bearer sk-my-token"

    @pytest.mark.asyncio
    async def test_chat_log_updated(self, entity, mock_user_input, mock_chat_log, patch_post):
        """The HA chat log records the assistant's response."""
        entity.entry.options["try_ha_first"] = False

        resp = make_orchestrator_response("Noted.")

        with patch_post(resp):
            await entity._async_handle_message(mock_user_input, mock_chat_log)

        mock_chat_log.async_add_assistant_content_without_tools.assert_called_once()

    @pytest.mark.asyncio
    async def test_http_error_returns_error_result(self, entity, mock_user_input, mock_chat_log, patch_post):
        """A non-200 from the orchestrator produces an error ConversationResult."""
        entity.entry.options["try_ha_first"] = False

        with patch_post(error_status=500, error_text="Internal Server Error"):
            result = await entity._async_handle_message(mock_user_input, mock_chat_log)

        result.response.async_set_error.assert_called_once()

    @pytest.mark.asyncio
    async def test_connection_error_returns_error_result(self, entity, mock_user_input, mock_chat_log, patch_post):
        """A connection failure (orchestrator unreachable) produces an error result, not a crash."""
        entity.entry.options["try_ha_first"] = False

        with patch_post(raises=True):
            result = await entity._async_handle_message(mock_user_input, mock_chat_log)

        result.response.async_set_error.assert_called_once()


# ===================================================================
# Tests: DefaultAgent fast path (try_ha_first)
# ===================================================================


class TestDefaultAgentFallback:
    """Tests for the try_ha_first -> DefaultAgent -> orchestrator flow."""

    @pytest.mark.asyncio
    async def test_ha_default_agent_match_skips_orchestrator(
        self, entity, mock_user_input, mock_chat_log, patch_post
    ):
        """When DefaultAgent matches an intent, the orchestrator is never called."""
        entity.entry.options["try_ha_first"] = True
        mock_user_input.text = "turn on the lights"

        mock_ha_result = MagicMock()
        mock_ha_result.response.response_type = "action_done"  # not ERROR

        with patch("custom_components.ha_jarvis.conversation.conversation") as mock_conv, \
             patch("custom_components.ha_jarvis.conversation.intent") as mock_intent:
            mock_intent.IntentResponseType.ERROR = "error"
            mock_conv.async_converse = AsyncMock(return_value=mock_ha_result)
            result = await entity._async_handle_message(mock_user_input, mock_chat_log)

        assert result is mock_ha_result

    @pytest.mark.asyncio
    async def test_ha_default_agent_no_match_falls_back(
        self, entity, mock_user_input, mock_chat_log, patch_post
    ):
        """When DefaultAgent doesn't match, the orchestrator is called."""
        entity.entry.options["try_ha_first"] = True
        mock_user_input.text = "what is the meaning of life?"

        mock_ha_result = MagicMock()
        mock_ha_result.response.response_type = "error"
        mock_ha_result.response.error_code = "no_intent_match"

        resp = make_orchestrator_response("42, of course!")
        intent_response_mock = MagicMock()

        with patch("custom_components.ha_jarvis.conversation.conversation") as mock_conv, \
             patch("custom_components.ha_jarvis.conversation.intent") as mock_intent, \
             patch_post(resp):
            mock_intent.IntentResponseType.ERROR = "error"
            mock_intent.IntentResponseErrorCode.UNKNOWN = "unknown"
            mock_intent.IntentResponse.return_value = intent_response_mock
            mock_conv.async_converse = AsyncMock(return_value=mock_ha_result)
            mock_conv.AssistantContent = MagicMock

            await entity._async_handle_message(mock_user_input, mock_chat_log)

        intent_response_mock.async_set_speech.assert_called_once_with("42, of course!")

    @pytest.mark.asyncio
    async def test_ha_default_agent_error_falls_back(
        self, entity, mock_user_input, mock_chat_log, patch_post
    ):
        """When DefaultAgent raises an exception, the orchestrator is still called."""
        entity.entry.options["try_ha_first"] = True
        mock_user_input.text = "do something weird"

        resp = make_orchestrator_response("I can help with that!")
        intent_response_mock = MagicMock()

        with patch("custom_components.ha_jarvis.conversation.conversation") as mock_conv, \
             patch("custom_components.ha_jarvis.conversation.intent") as mock_intent, \
             patch_post(resp):
            mock_intent.IntentResponseType.ERROR = "error"
            mock_intent.IntentResponseErrorCode.UNKNOWN = "unknown"
            mock_intent.IntentResponse.return_value = intent_response_mock
            mock_conv.async_converse = AsyncMock(side_effect=RuntimeError("boom"))
            mock_conv.AssistantContent = MagicMock

            await entity._async_handle_message(mock_user_input, mock_chat_log)

        intent_response_mock.async_set_speech.assert_called_once_with(
            "I can help with that!"
        )

    @pytest.mark.asyncio
    async def test_try_ha_first_disabled_skips_default_agent(
        self, entity, mock_user_input, mock_chat_log, patch_post
    ):
        """When try_ha_first is False, DefaultAgent is never consulted."""
        entity.entry.options["try_ha_first"] = False

        resp = make_orchestrator_response("Hello!")

        with patch("custom_components.ha_jarvis.conversation.conversation") as mock_conv, \
             patch_post(resp):
            mock_conv.async_converse = AsyncMock()
            mock_conv.AssistantContent = MagicMock

            await entity._async_handle_message(mock_user_input, mock_chat_log)

        mock_conv.async_converse.assert_not_called()
