"""Tests for the HA Jarvis conversation agent."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.ha_jarvis.conversation import (
    JarvisConversationEntity,
    _format_tool,
)
from custom_components.ha_jarvis.const import (
    DEFAULT_PROMPT,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_P,
    DEFAULT_KEEP_ALIVE,
    MAX_TOOL_ITERATIONS,
)

from .conftest import (
    make_ollama_response,
    make_openai_response,
    make_openai_tool_call,
    make_tool_call,
)


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
    """Mimics aiohttp.ClientSession with configurable responses."""

    def __init__(self, responses: list[dict], capture: list | None = None):
        self._responses = responses
        self._call_index = 0
        self._capture = capture

    def post(self, url, *, json=None, headers=None, timeout=None):
        if self._capture is not None:
            self._capture.append(json)
        idx = min(self._call_index, len(self._responses) - 1)
        resp = _FakeResponse(self._responses[idx])
        self._call_index += 1
        return _FakeResponseCtx(resp)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class _FakeErrorSession:
    """Mimics aiohttp.ClientSession that returns HTTP errors."""

    def __init__(self, status: int, text: str):
        self._status = status
        self._text = text

    def post(self, url, *, json=None, headers=None, timeout=None):
        resp = _FakeResponse({}, status=self._status)
        resp._text = self._text

        async def _text():
            return self._text
        resp.text = _text
        return _FakeResponseCtx(resp)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def entity(mock_hass, mock_config_entry):
    """Create a JarvisConversationEntity with mocked HA dependencies."""
    return JarvisConversationEntity(mock_config_entry, mock_hass)


@pytest.fixture
def patch_aiohttp():
    """Provide a helper to patch aiohttp.ClientSession with fake responses.

    Usage:
        with patch_aiohttp([resp1, resp2]) as captured:
            ...  # captured is a list of payloads sent to Ollama
    """
    from contextlib import contextmanager

    @contextmanager
    def _patch(responses: list[dict], *, error_status: int | None = None, error_text: str = ""):
        captured: list[dict] = []
        if error_status is not None:
            session = _FakeErrorSession(error_status, error_text)
        else:
            session = _FakeSession(responses, capture=captured)

        with patch("aiohttp.ClientSession", return_value=session):
            yield captured

    return _patch


# ===================================================================
# Tests: Generic conversation (no tool calls)
# ===================================================================


class TestGenericConversation:
    """Tests for simple text-only Ollama responses (no tool calling)."""

    @pytest.mark.asyncio
    async def test_simple_response(self, entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """Ollama returns a plain text response with no tool calls."""
        mock_user_input.text = "What is the weather like?"
        entity.entry.options["try_ha_first"] = False

        ollama_resp = make_ollama_response("It looks sunny outside!")

        with patch_aiohttp([ollama_resp]), \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm:
            mock_llm.async_get_api = AsyncMock(return_value=None)
            result = await entity._async_handle_message(mock_user_input, mock_chat_log)

        assert result is not None
        result.response.async_set_speech.assert_called_once_with("It looks sunny outside!")

    @pytest.mark.asyncio
    async def test_conversation_history_is_stored(self, entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """After a response, history should contain the user+assistant exchange."""
        mock_user_input.text = "Tell me a joke"
        mock_user_input.conversation_id = "conv-1"
        entity.entry.options["try_ha_first"] = False

        ollama_resp = make_ollama_response("Why did the chicken cross the road?")

        with patch_aiohttp([ollama_resp]), \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm:
            mock_llm.async_get_api = AsyncMock(return_value=None)
            await entity._async_handle_message(mock_user_input, mock_chat_log)

        history = entity._conversation_history.get("conv-1", [])
        assert len(history) == 2
        assert history[0] == {"role": "user", "content": "Tell me a joke"}
        assert history[1] == {"role": "assistant", "content": "Why did the chicken cross the road?"}

    @pytest.mark.asyncio
    async def test_history_trimming(self, entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """History should be trimmed to max_history turns."""
        entity.entry.options["try_ha_first"] = False
        entity.entry.options["max_history"] = 2  # 2 turns = 4 messages
        mock_user_input.conversation_id = "conv-trim"

        with patch("custom_components.ha_jarvis.conversation.llm") as mock_llm:
            mock_llm.async_get_api = AsyncMock(return_value=None)

            for i in range(5):
                mock_user_input.text = f"Message {i}"
                ollama_resp = make_ollama_response(f"Response {i}")
                with patch_aiohttp([ollama_resp]):
                    await entity._async_handle_message(mock_user_input, mock_chat_log)

        history = entity._conversation_history["conv-trim"]
        assert len(history) == 4
        assert history[0]["content"] == "Message 3"
        assert history[1]["content"] == "Response 3"

    @pytest.mark.asyncio
    async def test_ollama_error_returns_error_result(self, entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """When Ollama returns an HTTP error, an error ConversationResult is returned."""
        entity.entry.options["try_ha_first"] = False

        with patch_aiohttp([], error_status=500, error_text="Internal Server Error"), \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm:
            mock_llm.async_get_api = AsyncMock(return_value=None)
            result = await entity._async_handle_message(mock_user_input, mock_chat_log)

        assert result is not None
        result.response.async_set_error.assert_called_once()

    @pytest.mark.asyncio
    async def test_ollama_payload_includes_model_and_options(self, entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """Verify the Ollama API payload has the right model/options."""
        entity.entry.options["try_ha_first"] = False

        ollama_resp = make_ollama_response("ok")

        with patch_aiohttp([ollama_resp]) as captured, \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm:
            mock_llm.async_get_api = AsyncMock(return_value=None)
            await entity._async_handle_message(mock_user_input, mock_chat_log)

        assert len(captured) == 1
        payload = captured[0]
        assert payload["model"] == "llama3.1"
        assert payload["stream"] is False
        assert payload["options"]["temperature"] == DEFAULT_TEMPERATURE
        assert payload["options"]["top_p"] == DEFAULT_TOP_P
        assert payload["keep_alive"] == DEFAULT_KEEP_ALIVE
        assert "tools" not in payload  # No tools when llm_api is None

    @pytest.mark.asyncio
    async def test_empty_response_handled(self, entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """An empty content string from Ollama should still work."""
        entity.entry.options["try_ha_first"] = False

        ollama_resp = make_ollama_response("")

        with patch_aiohttp([ollama_resp]), \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm:
            mock_llm.async_get_api = AsyncMock(return_value=None)
            result = await entity._async_handle_message(mock_user_input, mock_chat_log)

        result.response.async_set_speech.assert_called_once_with("")


# ===================================================================
# Tests: HA tool-call scenarios
# ===================================================================


class TestToolCalling:
    """Tests for Ollama tool-calling with HA intents."""

    def _make_mock_llm_api(self, tools: list | None = None):
        """Create a mock LLM API instance with tools."""
        api = MagicMock()
        api.tools = tools or []
        api.api_prompt = "You can control smart home devices."
        api.llm_context = MagicMock()
        return api

    def _make_mock_tool(self, name: str, description: str = "", result: dict | None = None):
        """Create a mock HA LLM tool."""
        tool = MagicMock()
        tool.name = name
        tool.description = description
        tool.parameters = {"type": "object", "properties": {"name": {"type": "string"}}}
        tool.async_call = AsyncMock(return_value=result or {"success": True})
        return tool

    @pytest.mark.asyncio
    async def test_single_tool_call_turn_on(self, entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """Ollama calls HassTurnOn, tool executes, LLM returns final text."""
        entity.entry.options["try_ha_first"] = False
        mock_user_input.text = "Please turn on the kitchen lights"

        tool = self._make_mock_tool(
            "HassTurnOn", "Turn on a device",
            result={"speech": {"plain": {"speech": "Turned on kitchen lights"}}}
        )
        llm_api = self._make_mock_llm_api(tools=[tool])

        resp1 = make_ollama_response(
            "",
            tool_calls=[make_tool_call("HassTurnOn", {"name": "kitchen lights"})]
        )
        resp2 = make_ollama_response("Done! I've turned on the kitchen lights.")

        with patch_aiohttp([resp1, resp2]), \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm:
            mock_llm.async_get_api = AsyncMock(return_value=llm_api)
            mock_llm.ToolInput = MagicMock
            result = await entity._async_handle_message(mock_user_input, mock_chat_log)

        tool.async_call.assert_called_once()
        result.response.async_set_speech.assert_called_once_with(
            "Done! I've turned on the kitchen lights."
        )

    @pytest.mark.asyncio
    async def test_single_tool_call_turn_off(self, entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """Ollama calls HassTurnOff tool."""
        entity.entry.options["try_ha_first"] = False
        mock_user_input.text = "Turn off the bedroom fan"

        tool = self._make_mock_tool(
            "HassTurnOff", "Turn off a device",
            result={"speech": {"plain": {"speech": "Turned off bedroom fan"}}}
        )
        llm_api = self._make_mock_llm_api(tools=[tool])

        resp1 = make_ollama_response(
            "",
            tool_calls=[make_tool_call("HassTurnOff", {"name": "bedroom fan"})]
        )
        resp2 = make_ollama_response("The bedroom fan is now off.")

        with patch_aiohttp([resp1, resp2]), \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm:
            mock_llm.async_get_api = AsyncMock(return_value=llm_api)
            mock_llm.ToolInput = MagicMock
            result = await entity._async_handle_message(mock_user_input, mock_chat_log)

        tool.async_call.assert_called_once()
        result.response.async_set_speech.assert_called_once_with("The bedroom fan is now off.")

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_in_sequence(self, entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """Ollama makes two sequential tool calls across two iterations."""
        entity.entry.options["try_ha_first"] = False
        mock_user_input.text = "Turn on living room and turn off kitchen"

        tool_on = self._make_mock_tool("HassTurnOn", "Turn on", result={"success": True})
        tool_off = self._make_mock_tool("HassTurnOff", "Turn off", result={"success": True})
        llm_api = self._make_mock_llm_api(tools=[tool_on, tool_off])

        resp1 = make_ollama_response(
            "", tool_calls=[make_tool_call("HassTurnOn", {"name": "living room lights"})]
        )
        resp2 = make_ollama_response(
            "", tool_calls=[make_tool_call("HassTurnOff", {"name": "kitchen lights"})]
        )
        resp3 = make_ollama_response("All done! Living room is on, kitchen is off.")

        with patch_aiohttp([resp1, resp2, resp3]), \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm:
            mock_llm.async_get_api = AsyncMock(return_value=llm_api)
            mock_llm.ToolInput = MagicMock
            result = await entity._async_handle_message(mock_user_input, mock_chat_log)

        tool_on.async_call.assert_called_once()
        tool_off.async_call.assert_called_once()
        result.response.async_set_speech.assert_called_once_with(
            "All done! Living room is on, kitchen is off."
        )

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_in_single_response(self, entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """Ollama returns two tool calls in a single response message."""
        entity.entry.options["try_ha_first"] = False
        mock_user_input.text = "Turn on both the living room and bedroom lights"

        tool = self._make_mock_tool("HassTurnOn", "Turn on", result={"success": True})
        llm_api = self._make_mock_llm_api(tools=[tool])

        resp1 = make_ollama_response(
            "",
            tool_calls=[
                make_tool_call("HassTurnOn", {"name": "living room lights"}),
                make_tool_call("HassTurnOn", {"name": "bedroom lights"}),
            ]
        )
        resp2 = make_ollama_response("Both lights are now on!")

        with patch_aiohttp([resp1, resp2]), \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm:
            mock_llm.async_get_api = AsyncMock(return_value=llm_api)
            mock_llm.ToolInput = MagicMock
            result = await entity._async_handle_message(mock_user_input, mock_chat_log)

        assert tool.async_call.call_count == 2
        result.response.async_set_speech.assert_called_once_with("Both lights are now on!")

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """When Ollama calls a tool that doesn't exist, an error is returned to the LLM."""
        entity.entry.options["try_ha_first"] = False

        llm_api = self._make_mock_llm_api(tools=[])

        resp1 = make_ollama_response(
            "", tool_calls=[make_tool_call("NonExistentTool", {"name": "test"})]
        )
        resp2 = make_ollama_response("I'm sorry, I couldn't do that.")

        with patch_aiohttp([resp1, resp2]), \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm:
            mock_llm.async_get_api = AsyncMock(return_value=llm_api)
            mock_llm.ToolInput = MagicMock
            result = await entity._async_handle_message(mock_user_input, mock_chat_log)

        result.response.async_set_speech.assert_called_once_with(
            "I'm sorry, I couldn't do that."
        )

    @pytest.mark.asyncio
    async def test_tool_execution_error_is_handled(self, entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """When a tool raises an exception, the error is sent back to Ollama."""
        entity.entry.options["try_ha_first"] = False

        tool = self._make_mock_tool("HassTurnOn", "Turn on")
        tool.async_call = AsyncMock(side_effect=RuntimeError("Device unavailable"))
        llm_api = self._make_mock_llm_api(tools=[tool])

        resp1 = make_ollama_response(
            "", tool_calls=[make_tool_call("HassTurnOn", {"name": "broken device"})]
        )
        resp2 = make_ollama_response("Sorry, the device seems to be unavailable.")

        with patch_aiohttp([resp1, resp2]), \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm:
            mock_llm.async_get_api = AsyncMock(return_value=llm_api)
            mock_llm.ToolInput = MagicMock
            result = await entity._async_handle_message(mock_user_input, mock_chat_log)

        result.response.async_set_speech.assert_called_once_with(
            "Sorry, the device seems to be unavailable."
        )

    @pytest.mark.asyncio
    async def test_tools_included_in_ollama_payload(self, entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """Verify that HA tools are passed in the Ollama payload when available."""
        entity.entry.options["try_ha_first"] = False

        tool = self._make_mock_tool("HassTurnOn", "Turn on a device")
        llm_api = self._make_mock_llm_api(tools=[tool])

        ollama_resp = make_ollama_response("ok")

        with patch_aiohttp([ollama_resp]) as captured, \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm:
            mock_llm.async_get_api = AsyncMock(return_value=llm_api)
            mock_llm.ToolInput = MagicMock
            await entity._async_handle_message(mock_user_input, mock_chat_log)

        assert len(captured) >= 1
        payload = captured[0]
        assert "tools" in payload
        assert len(payload["tools"]) == 1
        assert payload["tools"][0]["function"]["name"] == "HassTurnOn"

    @pytest.mark.asyncio
    async def test_system_prompt_includes_llm_api_prompt(self, entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """System prompt should be augmented with the LLM API entity context."""
        entity.entry.options["try_ha_first"] = False

        llm_api = self._make_mock_llm_api(tools=[])
        llm_api.api_prompt = "You have access to these entities: light.kitchen"

        ollama_resp = make_ollama_response("ok")

        with patch_aiohttp([ollama_resp]) as captured, \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm:
            mock_llm.async_get_api = AsyncMock(return_value=llm_api)
            await entity._async_handle_message(mock_user_input, mock_chat_log)

        system_msg = captured[0]["messages"][0]
        assert system_msg["role"] == "system"
        assert DEFAULT_PROMPT in system_msg["content"]
        assert "light.kitchen" in system_msg["content"]

    @pytest.mark.asyncio
    async def test_max_tool_iterations_safety(self, entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """If the LLM keeps calling tools beyond MAX_TOOL_ITERATIONS, we break out."""
        entity.entry.options["try_ha_first"] = False

        tool = self._make_mock_tool("HassTurnOn", "Turn on", result={"success": True})
        llm_api = self._make_mock_llm_api(tools=[tool])

        # Every response is a tool call - should stop at MAX_TOOL_ITERATIONS
        infinite_tool_resp = make_ollama_response(
            "", tool_calls=[make_tool_call("HassTurnOn", {"name": "light"})]
        )
        final_resp = make_ollama_response("I've been trying but something went wrong.")
        responses = [infinite_tool_resp] * (MAX_TOOL_ITERATIONS + 1) + [final_resp]

        with patch_aiohttp(responses), \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm:
            mock_llm.async_get_api = AsyncMock(return_value=llm_api)
            mock_llm.ToolInput = MagicMock
            result = await entity._async_handle_message(mock_user_input, mock_chat_log)

        assert tool.async_call.call_count == MAX_TOOL_ITERATIONS
        assert result is not None

    @pytest.mark.asyncio
    async def test_no_llm_api_falls_back_to_text_only(self, entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """When llm.async_get_api raises, we still get a text response (no tools)."""
        entity.entry.options["try_ha_first"] = False

        ollama_resp = make_ollama_response("I can still chat without tools!")

        with patch_aiohttp([ollama_resp]) as captured, \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm:
            mock_llm.async_get_api = AsyncMock(side_effect=RuntimeError("LLM API unavailable"))
            result = await entity._async_handle_message(mock_user_input, mock_chat_log)

        result.response.async_set_speech.assert_called_once_with("I can still chat without tools!")
        assert "tools" not in captured[0]


# ===================================================================
# Tests: DefaultAgent integration (try_ha_first)
# ===================================================================


class TestDefaultAgentFallback:
    """Tests for the try_ha_first -> DefaultAgent -> Ollama flow."""

    @pytest.mark.asyncio
    async def test_ha_default_agent_match_skips_ollama(self, entity, mock_user_input, mock_chat_log):
        """When DefaultAgent matches an intent, Ollama is not called."""
        entity.entry.options["try_ha_first"] = True
        mock_user_input.text = "turn on the lights"

        mock_ha_result = MagicMock()
        mock_ha_result.response.response_type = "action_done"  # Not ERROR

        with patch("custom_components.ha_jarvis.conversation.conversation") as mock_conv, \
             patch("custom_components.ha_jarvis.conversation.intent") as mock_intent:
            mock_intent.IntentResponseType.ERROR = "error"
            mock_conv.async_converse = AsyncMock(return_value=mock_ha_result)
            result = await entity._async_handle_message(mock_user_input, mock_chat_log)

        assert result is mock_ha_result

    @pytest.mark.asyncio
    async def test_ha_default_agent_no_match_falls_back(self, entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """When DefaultAgent doesn't match, Ollama is called."""
        entity.entry.options["try_ha_first"] = True
        mock_user_input.text = "what is the meaning of life?"

        mock_ha_result = MagicMock()
        mock_ha_result.response.response_type = "error"
        mock_ha_result.response.error_code = "no_intent_match"

        ollama_resp = make_ollama_response("42, of course!")

        # Track the IntentResponse mock that will be created inside _async_handle_message
        intent_response_mock = MagicMock()

        with patch("custom_components.ha_jarvis.conversation.conversation") as mock_conv, \
             patch("custom_components.ha_jarvis.conversation.intent") as mock_intent, \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm, \
             patch_aiohttp([ollama_resp]):
            mock_intent.IntentResponseType.ERROR = "error"
            mock_intent.IntentResponseErrorCode.UNKNOWN = "unknown"
            mock_intent.IntentResponse.return_value = intent_response_mock
            mock_conv.async_converse = AsyncMock(return_value=mock_ha_result)
            mock_conv.AssistantContent = MagicMock
            mock_llm.async_get_api = AsyncMock(return_value=None)

            await entity._async_handle_message(mock_user_input, mock_chat_log)

        intent_response_mock.async_set_speech.assert_called_once_with("42, of course!")

    @pytest.mark.asyncio
    async def test_ha_default_agent_error_falls_back(self, entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """When DefaultAgent raises an exception, Ollama is called."""
        entity.entry.options["try_ha_first"] = True
        mock_user_input.text = "do something weird"

        ollama_resp = make_ollama_response("I can help with that!")

        intent_response_mock = MagicMock()

        with patch("custom_components.ha_jarvis.conversation.conversation") as mock_conv, \
             patch("custom_components.ha_jarvis.conversation.intent") as mock_intent, \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm, \
             patch_aiohttp([ollama_resp]):
            mock_intent.IntentResponseType.ERROR = "error"
            mock_intent.IntentResponseErrorCode.UNKNOWN = "unknown"
            mock_intent.IntentResponse.return_value = intent_response_mock
            mock_conv.async_converse = AsyncMock(side_effect=RuntimeError("boom"))
            mock_conv.AssistantContent = MagicMock
            mock_llm.async_get_api = AsyncMock(return_value=None)

            await entity._async_handle_message(mock_user_input, mock_chat_log)

        intent_response_mock.async_set_speech.assert_called_once_with("I can help with that!")

    @pytest.mark.asyncio
    async def test_try_ha_first_disabled_skips_default_agent(self, entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """When try_ha_first is False, DefaultAgent is never consulted."""
        entity.entry.options["try_ha_first"] = False

        ollama_resp = make_ollama_response("Hello!")

        with patch("custom_components.ha_jarvis.conversation.conversation") as mock_conv, \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm, \
             patch_aiohttp([ollama_resp]):
            mock_conv.async_converse = AsyncMock()
            mock_conv.AssistantContent = MagicMock
            mock_llm.async_get_api = AsyncMock(return_value=None)

            await entity._async_handle_message(mock_user_input, mock_chat_log)

        mock_conv.async_converse.assert_not_called()


# ===================================================================
# Tests: _format_tool utility
# ===================================================================


class TestFormatTool:
    """Tests for the _format_tool helper function."""

    def test_format_tool_structure(self):
        """_format_tool should produce the correct Ollama tool schema."""
        tool = MagicMock()
        tool.name = "HassTurnOn"
        tool.description = "Turn on a device"
        tool.parameters = {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Device name"}},
        }

        result = _format_tool(tool)

        assert result == {
            "type": "function",
            "function": {
                "name": "HassTurnOn",
                "description": "Turn on a device",
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string", "description": "Device name"}},
                },
            },
        }

    def test_format_tool_empty_description(self):
        """_format_tool handles None description."""
        tool = MagicMock()
        tool.name = "HassGetState"
        tool.description = None
        tool.parameters = {"type": "object", "properties": {}}

        result = _format_tool(tool)

        assert result["function"]["description"] == ""


# ===================================================================
# Tests: _call_ollama directly
# ===================================================================


class TestCallOllama:
    """Tests for the low-level _call_ollama method."""

    @pytest.mark.asyncio
    async def test_call_ollama_without_tools(self, entity, patch_aiohttp):
        """_call_ollama without tools should not include tools key in payload."""
        ollama_resp = make_ollama_response("hi")

        with patch_aiohttp([ollama_resp]) as captured:
            result = await entity._call_ollama(
                [{"role": "user", "content": "hi"}], tools=None
            )

        assert "tools" not in captured[0]
        assert result["message"]["content"] == "hi"

    @pytest.mark.asyncio
    async def test_call_ollama_with_tools(self, entity, patch_aiohttp):
        """_call_ollama with tools should include them in payload."""
        tools = [{"type": "function", "function": {"name": "test", "description": "", "parameters": {}}}]
        ollama_resp = make_ollama_response("hi")

        with patch_aiohttp([ollama_resp]) as captured:
            await entity._call_ollama(
                [{"role": "user", "content": "hi"}], tools=tools
            )

        assert captured[0]["tools"] == tools

    @pytest.mark.asyncio
    async def test_call_ollama_raises_on_http_error(self, entity, patch_aiohttp):
        """_call_ollama should raise RuntimeError on non-200 status."""
        with patch_aiohttp([], error_status=503, error_text="Service Unavailable"):
            with pytest.raises(RuntimeError, match="Ollama returned status 503"):
                await entity._call_ollama([{"role": "user", "content": "hi"}])


# ===================================================================
# Fixtures: OpenAI backend
# ===================================================================


@pytest.fixture
def openai_entity(mock_hass, mock_openai_config_entry):
    """Create a JarvisConversationEntity configured for the OpenAI backend."""
    return JarvisConversationEntity(mock_openai_config_entry, mock_hass)


# ===================================================================
# Tests: OpenAI-compatible conversation (no tool calls)
# ===================================================================


class TestOpenAIConversation:
    """Tests for simple text-only OpenAI-compatible responses."""

    @pytest.mark.asyncio
    async def test_simple_response(self, openai_entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """OpenAI backend returns a plain text response."""
        mock_user_input.text = "What is the weather like?"
        openai_entity.entry.options["try_ha_first"] = False

        openai_resp = make_openai_response("It looks sunny outside!")

        with patch_aiohttp([openai_resp]), \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm:
            mock_llm.async_get_api = AsyncMock(return_value=None)
            result = await openai_entity._async_handle_message(mock_user_input, mock_chat_log)

        result.response.async_set_speech.assert_called_once_with("It looks sunny outside!")

    @pytest.mark.asyncio
    async def test_conversation_history_stored(self, openai_entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """After an OpenAI response, history should contain the user+assistant exchange."""
        mock_user_input.text = "Tell me a joke"
        mock_user_input.conversation_id = "conv-openai-1"
        openai_entity.entry.options["try_ha_first"] = False

        openai_resp = make_openai_response("Why did the chicken cross the road?")

        with patch_aiohttp([openai_resp]), \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm:
            mock_llm.async_get_api = AsyncMock(return_value=None)
            await openai_entity._async_handle_message(mock_user_input, mock_chat_log)

        history = openai_entity._conversation_history.get("conv-openai-1", [])
        assert len(history) == 2
        assert history[0] == {"role": "user", "content": "Tell me a joke"}
        assert history[1] == {"role": "assistant", "content": "Why did the chicken cross the road?"}

    @pytest.mark.asyncio
    async def test_openai_payload_structure(self, openai_entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """Verify the OpenAI API payload has model, temperature, top_p, and no keep_alive."""
        openai_entity.entry.options["try_ha_first"] = False

        openai_resp = make_openai_response("ok")

        with patch_aiohttp([openai_resp]) as captured, \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm:
            mock_llm.async_get_api = AsyncMock(return_value=None)
            await openai_entity._async_handle_message(mock_user_input, mock_chat_log)

        assert len(captured) == 1
        payload = captured[0]
        assert payload["model"] == "assistant"
        assert payload["stream"] is False
        assert payload["temperature"] == DEFAULT_TEMPERATURE
        assert payload["top_p"] == DEFAULT_TOP_P
        assert "keep_alive" not in payload
        assert "options" not in payload
        assert "tools" not in payload

    @pytest.mark.asyncio
    async def test_openai_error_returns_error_result(self, openai_entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """When OpenAI returns an HTTP error, an error ConversationResult is returned."""
        openai_entity.entry.options["try_ha_first"] = False

        with patch_aiohttp([], error_status=500, error_text="Internal Server Error"), \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm:
            mock_llm.async_get_api = AsyncMock(return_value=None)
            result = await openai_entity._async_handle_message(mock_user_input, mock_chat_log)

        result.response.async_set_error.assert_called_once()


# ===================================================================
# Tests: OpenAI tool-call scenarios
# ===================================================================


class TestOpenAIToolCalling:
    """Tests for OpenAI-compatible tool-calling with HA intents."""

    def _make_mock_llm_api(self, tools: list | None = None):
        """Create a mock LLM API instance with tools."""
        api = MagicMock()
        api.tools = tools or []
        api.api_prompt = "You can control smart home devices."
        api.llm_context = MagicMock()
        return api

    def _make_mock_tool(self, name: str, description: str = "", result: dict | None = None):
        """Create a mock HA LLM tool."""
        tool = MagicMock()
        tool.name = name
        tool.description = description
        tool.parameters = {"type": "object", "properties": {"name": {"type": "string"}}}
        tool.async_call = AsyncMock(return_value=result or {"success": True})
        return tool

    @pytest.mark.asyncio
    async def test_single_tool_call(self, openai_entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """OpenAI calls HassTurnOn with tool_call_id round-trip."""
        openai_entity.entry.options["try_ha_first"] = False
        mock_user_input.text = "Turn on the kitchen lights"

        tool = self._make_mock_tool(
            "HassTurnOn", "Turn on a device",
            result={"speech": {"plain": {"speech": "Turned on kitchen lights"}}},
        )
        llm_api = self._make_mock_llm_api(tools=[tool])

        resp1 = make_openai_response(
            "",
            tool_calls=[make_openai_tool_call("HassTurnOn", {"name": "kitchen lights"}, "call_abc")]
        )
        resp2 = make_openai_response("Done! I've turned on the kitchen lights.")

        with patch_aiohttp([resp1, resp2]) as captured, \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm:
            mock_llm.async_get_api = AsyncMock(return_value=llm_api)
            mock_llm.ToolInput = MagicMock
            result = await openai_entity._async_handle_message(mock_user_input, mock_chat_log)

        tool.async_call.assert_called_once()
        result.response.async_set_speech.assert_called_once_with(
            "Done! I've turned on the kitchen lights."
        )

        # Verify tool result message includes tool_call_id
        assert len(captured) >= 2
        tool_result_payload = captured[1]
        tool_msgs = [m for m in tool_result_payload["messages"] if m.get("role") == "tool"]
        assert len(tool_msgs) >= 1
        assert tool_msgs[0]["tool_call_id"] == "call_abc"

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_in_sequence(self, openai_entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """OpenAI makes two sequential tool calls across two iterations."""
        openai_entity.entry.options["try_ha_first"] = False
        mock_user_input.text = "Turn on living room and turn off kitchen"

        tool_on = self._make_mock_tool("HassTurnOn", "Turn on", result={"success": True})
        tool_off = self._make_mock_tool("HassTurnOff", "Turn off", result={"success": True})
        llm_api = self._make_mock_llm_api(tools=[tool_on, tool_off])

        resp1 = make_openai_response(
            "", tool_calls=[make_openai_tool_call("HassTurnOn", {"name": "living room lights"}, "call_1")]
        )
        resp2 = make_openai_response(
            "", tool_calls=[make_openai_tool_call("HassTurnOff", {"name": "kitchen lights"}, "call_2")]
        )
        resp3 = make_openai_response("All done! Living room is on, kitchen is off.")

        with patch_aiohttp([resp1, resp2, resp3]), \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm:
            mock_llm.async_get_api = AsyncMock(return_value=llm_api)
            mock_llm.ToolInput = MagicMock
            result = await openai_entity._async_handle_message(mock_user_input, mock_chat_log)

        tool_on.async_call.assert_called_once()
        tool_off.async_call.assert_called_once()
        result.response.async_set_speech.assert_called_once_with(
            "All done! Living room is on, kitchen is off."
        )

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_in_single_response(self, openai_entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """OpenAI returns two tool calls in a single response message."""
        openai_entity.entry.options["try_ha_first"] = False
        mock_user_input.text = "Turn on both lights"

        tool = self._make_mock_tool("HassTurnOn", "Turn on", result={"success": True})
        llm_api = self._make_mock_llm_api(tools=[tool])

        resp1 = make_openai_response(
            "",
            tool_calls=[
                make_openai_tool_call("HassTurnOn", {"name": "living room lights"}, "call_a"),
                make_openai_tool_call("HassTurnOn", {"name": "bedroom lights"}, "call_b"),
            ]
        )
        resp2 = make_openai_response("Both lights are now on!")

        with patch_aiohttp([resp1, resp2]), \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm:
            mock_llm.async_get_api = AsyncMock(return_value=llm_api)
            mock_llm.ToolInput = MagicMock
            result = await openai_entity._async_handle_message(mock_user_input, mock_chat_log)

        assert tool.async_call.call_count == 2
        result.response.async_set_speech.assert_called_once_with("Both lights are now on!")

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, openai_entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """When OpenAI calls a tool that doesn't exist, an error is returned to the LLM."""
        openai_entity.entry.options["try_ha_first"] = False

        llm_api = self._make_mock_llm_api(tools=[])

        resp1 = make_openai_response(
            "", tool_calls=[make_openai_tool_call("NonExistentTool", {"name": "test"}, "call_err")]
        )
        resp2 = make_openai_response("I'm sorry, I couldn't do that.")

        with patch_aiohttp([resp1, resp2]), \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm:
            mock_llm.async_get_api = AsyncMock(return_value=llm_api)
            mock_llm.ToolInput = MagicMock
            result = await openai_entity._async_handle_message(mock_user_input, mock_chat_log)

        result.response.async_set_speech.assert_called_once_with(
            "I'm sorry, I couldn't do that."
        )

    @pytest.mark.asyncio
    async def test_tool_execution_error_is_handled(self, openai_entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """When a tool raises an exception, the error is sent back to the LLM."""
        openai_entity.entry.options["try_ha_first"] = False

        tool = self._make_mock_tool("HassTurnOn", "Turn on")
        tool.async_call = AsyncMock(side_effect=RuntimeError("Device unavailable"))
        llm_api = self._make_mock_llm_api(tools=[tool])

        resp1 = make_openai_response(
            "", tool_calls=[make_openai_tool_call("HassTurnOn", {"name": "broken"}, "call_x")]
        )
        resp2 = make_openai_response("Sorry, the device seems to be unavailable.")

        with patch_aiohttp([resp1, resp2]), \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm:
            mock_llm.async_get_api = AsyncMock(return_value=llm_api)
            mock_llm.ToolInput = MagicMock
            result = await openai_entity._async_handle_message(mock_user_input, mock_chat_log)

        result.response.async_set_speech.assert_called_once_with(
            "Sorry, the device seems to be unavailable."
        )

    @pytest.mark.asyncio
    async def test_max_tool_iterations_safety(self, openai_entity, mock_user_input, mock_chat_log, patch_aiohttp):
        """If the LLM keeps calling tools beyond MAX_TOOL_ITERATIONS, we break out."""
        openai_entity.entry.options["try_ha_first"] = False

        tool = self._make_mock_tool("HassTurnOn", "Turn on", result={"success": True})
        llm_api = self._make_mock_llm_api(tools=[tool])

        infinite_tool_resp = make_openai_response(
            "", tool_calls=[make_openai_tool_call("HassTurnOn", {"name": "light"}, "call_loop")]
        )
        final_resp = make_openai_response("I've been trying but something went wrong.")
        responses = [infinite_tool_resp] * (MAX_TOOL_ITERATIONS + 1) + [final_resp]

        with patch_aiohttp(responses), \
             patch("custom_components.ha_jarvis.conversation.llm") as mock_llm:
            mock_llm.async_get_api = AsyncMock(return_value=llm_api)
            mock_llm.ToolInput = MagicMock
            result = await openai_entity._async_handle_message(mock_user_input, mock_chat_log)

        assert tool.async_call.call_count == MAX_TOOL_ITERATIONS
        assert result is not None


# ===================================================================
# Tests: _call_openai directly
# ===================================================================


class TestCallOpenAI:
    """Tests for the low-level _call_openai method."""

    @pytest.mark.asyncio
    async def test_call_openai_without_tools(self, openai_entity, patch_aiohttp):
        """_call_openai without tools should not include tools key in payload."""
        openai_resp = make_openai_response("hi")

        with patch_aiohttp([openai_resp]) as captured:
            result = await openai_entity._call_openai(
                [{"role": "user", "content": "hi"}], tools=None
            )

        assert "tools" not in captured[0]
        assert result["content"] == "hi"

    @pytest.mark.asyncio
    async def test_call_openai_with_tools(self, openai_entity, patch_aiohttp):
        """_call_openai with tools should include them in payload."""
        tools = [{"type": "function", "function": {"name": "test", "description": "", "parameters": {}}}]
        openai_resp = make_openai_response("hi")

        with patch_aiohttp([openai_resp]) as captured:
            await openai_entity._call_openai(
                [{"role": "user", "content": "hi"}], tools=tools
            )

        assert captured[0]["tools"] == tools

    @pytest.mark.asyncio
    async def test_call_openai_raises_on_http_error(self, openai_entity, patch_aiohttp):
        """_call_openai should raise RuntimeError on non-200 status."""
        with patch_aiohttp([], error_status=503, error_text="Service Unavailable"):
            with pytest.raises(RuntimeError, match="OpenAI API returned status 503"):
                await openai_entity._call_openai([{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_call_openai_returns_message_dict(self, openai_entity, patch_aiohttp):
        """_call_openai should return the message dict from choices[0]."""
        openai_resp = make_openai_response("hello world")

        with patch_aiohttp([openai_resp]):
            result = await openai_entity._call_openai(
                [{"role": "user", "content": "hi"}]
            )

        assert result["role"] == "assistant"
        assert result["content"] == "hello world"
