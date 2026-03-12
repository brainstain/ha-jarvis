"""Shared test fixtures for ha_jarvis tests.

Mocks all homeassistant.* modules so the component can be imported without
a real HA installation.
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub out every homeassistant.* module the component imports
# ---------------------------------------------------------------------------

_HA_MODULES: dict[str, ModuleType] = {}


def _make_module(name: str) -> ModuleType:
    mod = ModuleType(name)
    _HA_MODULES[name] = mod
    return mod


# homeassistant top-level
ha = _make_module("homeassistant")

# homeassistant.const
ha_const = _make_module("homeassistant.const")
ha_const.MATCH_ALL = "*"  # type: ignore[attr-defined]
ha_const.Platform = MagicMock()  # type: ignore[attr-defined]
ha_const.Platform.CONVERSATION = "conversation"  # type: ignore[attr-defined]

# homeassistant.core
ha_core = _make_module("homeassistant.core")
ha_core.HomeAssistant = MagicMock  # type: ignore[attr-defined]

# homeassistant.config_entries
ha_config_entries = _make_module("homeassistant.config_entries")
ha_config_entries.ConfigEntry = MagicMock  # type: ignore[attr-defined]
ha_config_entries.ConfigFlow = type("ConfigFlow", (), {})  # type: ignore[attr-defined]
ha_config_entries.ConfigFlowResult = MagicMock  # type: ignore[attr-defined]
ha_config_entries.OptionsFlow = type("OptionsFlow", (), {})  # type: ignore[attr-defined]

# homeassistant.exceptions
ha_exceptions = _make_module("homeassistant.exceptions")
ha_exceptions.ConfigEntryNotReady = type("ConfigEntryNotReady", (Exception,), {})  # type: ignore[attr-defined]

# homeassistant.helpers
ha_helpers = _make_module("homeassistant.helpers")

# homeassistant.helpers.entity_platform
ha_entity_platform = _make_module("homeassistant.helpers.entity_platform")
ha_entity_platform.AddEntitiesCallback = MagicMock  # type: ignore[attr-defined]

# homeassistant.helpers.intent
ha_intent = _make_module("homeassistant.helpers.intent")
_IntentResponse = MagicMock
ha_intent.IntentResponse = _IntentResponse  # type: ignore[attr-defined]
ha_intent.IntentResponseType = MagicMock()  # type: ignore[attr-defined]
ha_intent.IntentResponseType.ERROR = "error"  # type: ignore[attr-defined]
ha_intent.IntentResponseErrorCode = MagicMock()  # type: ignore[attr-defined]
ha_intent.IntentResponseErrorCode.UNKNOWN = "unknown"  # type: ignore[attr-defined]

# homeassistant.helpers.llm
ha_llm = _make_module("homeassistant.helpers.llm")
ha_llm.async_get_api = AsyncMock()  # type: ignore[attr-defined]
ha_llm.ToolInput = MagicMock  # type: ignore[attr-defined]
ha_llm.Tool = type("Tool", (), {})  # type: ignore[attr-defined]
ha_llm.APIInstance = MagicMock  # type: ignore[attr-defined]

# homeassistant.components
ha_components = _make_module("homeassistant.components")

# homeassistant.components.conversation  (the big one)
ha_conversation = _make_module("homeassistant.components.conversation")

# Build a real-looking ConversationEntity base class
class _ConversationEntity:
    async def async_added_to_hass(self):
        pass

    async def async_will_remove_from_hass(self):
        pass

ha_conversation.ConversationEntity = _ConversationEntity  # type: ignore[attr-defined]
ha_conversation.ConversationInput = MagicMock  # type: ignore[attr-defined]
ha_conversation.ChatLog = MagicMock  # type: ignore[attr-defined]
ha_conversation.ConversationResult = MagicMock  # type: ignore[attr-defined]
ha_conversation.AssistantContent = MagicMock  # type: ignore[attr-defined]
ha_conversation.ConversationEntityFeature = MagicMock()  # type: ignore[attr-defined]
ha_conversation.ConversationEntityFeature.CONTROL = 1  # type: ignore[attr-defined]
ha_conversation.async_set_agent = MagicMock()  # type: ignore[attr-defined]
ha_conversation.async_unset_agent = MagicMock()  # type: ignore[attr-defined]
ha_conversation.async_converse = AsyncMock()  # type: ignore[attr-defined]

# homeassistant.components.conversation.trace
ha_trace = _make_module("homeassistant.components.conversation.trace")
ha_trace.async_conversation_trace_append = MagicMock()  # type: ignore[attr-defined]
ha_trace.ConversationTraceEventType = MagicMock()  # type: ignore[attr-defined]
ha_trace.ConversationTraceEventType.AGENT_DETAIL = "agent_detail"  # type: ignore[attr-defined]
ha_trace.ConversationTraceEventType.TOOL_CALL = "tool_call"  # type: ignore[attr-defined]

# homeassistant.components.conversation.const
ha_conv_const = _make_module("homeassistant.components.conversation.const")
ha_conv_const.HOME_ASSISTANT_AGENT = "homeassistant"  # type: ignore[attr-defined]

# homeassistant.core callback decorator (used by config_flow)
ha_core.callback = lambda f: f  # type: ignore[attr-defined]

# voluptuous (used by config_flow, not our concern in tests)
# -- already installed or not needed for conversation tests --

# Install all stubs into sys.modules
sys.modules.update(_HA_MODULES)


# ---------------------------------------------------------------------------
# Now we can safely import the component
# ---------------------------------------------------------------------------
from custom_components.ha_jarvis.const import (  # noqa: E402
    CONF_API_KEY,
    CONF_API_TYPE,
    CONF_MODEL,
    CONF_MODEL_NAME,
    DEFAULT_API_KEY,
    DEFAULT_API_TYPE,
    DEFAULT_KEEP_ALIVE,
    DEFAULT_MAX_HISTORY,
    DEFAULT_MODEL,
    DEFAULT_MODEL_NAME,
    DEFAULT_PROMPT,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_P,
    DEFAULT_TRY_HA_FIRST,
    DOMAIN,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.data = {DOMAIN: {"test-entry-id": {"base_url": "http://localhost:11434"}}}
    return hass


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry (Ollama backend by default)."""
    entry = MagicMock()
    entry.entry_id = "test-entry-id"
    entry.data = {CONF_MODEL: DEFAULT_MODEL, CONF_API_TYPE: "ollama"}
    entry.options = {
        "prompt": DEFAULT_PROMPT,
        "max_history": DEFAULT_MAX_HISTORY,
        "temperature": DEFAULT_TEMPERATURE,
        "top_p": DEFAULT_TOP_P,
        "keep_alive": DEFAULT_KEEP_ALIVE,
        "try_ha_first": DEFAULT_TRY_HA_FIRST,
    }
    return entry


@pytest.fixture
def mock_openai_config_entry():
    """Create a mock config entry for OpenAI-compatible backend."""
    entry = MagicMock()
    entry.entry_id = "test-entry-id"
    entry.data = {
        CONF_API_TYPE: "openai",
        CONF_MODEL_NAME: DEFAULT_MODEL_NAME,
        CONF_API_KEY: DEFAULT_API_KEY,
    }
    entry.options = {
        "prompt": DEFAULT_PROMPT,
        "max_history": DEFAULT_MAX_HISTORY,
        "temperature": DEFAULT_TEMPERATURE,
        "top_p": DEFAULT_TOP_P,
        "keep_alive": DEFAULT_KEEP_ALIVE,
        "try_ha_first": False,
    }
    return entry


@pytest.fixture
def mock_user_input():
    """Create a mock ConversationInput."""
    user_input = MagicMock()
    user_input.text = "Hello, how are you?"
    user_input.conversation_id = "test-conv-123"
    user_input.context = MagicMock()
    user_input.language = "en"
    user_input.agent_id = "test-agent"
    user_input.device_id = "test-device"
    user_input.as_llm_context.return_value = MagicMock()
    return user_input


@pytest.fixture
def mock_chat_log():
    """Create a mock ChatLog."""
    chat_log = MagicMock()
    chat_log.async_add_assistant_content_without_tools = MagicMock()
    return chat_log


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_ollama_response(content: str, tool_calls: list[dict] | None = None) -> dict:
    """Helper to build a mock Ollama API response."""
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"message": message, "done": True}


def make_tool_call(name: str, arguments: dict) -> dict:
    """Helper to build a tool_call dict matching Ollama's format."""
    return {"function": {"name": name, "arguments": arguments}}


def make_openai_response(
    content: str, tool_calls: list[dict] | None = None
) -> dict:
    """Helper to build a mock OpenAI-compatible API response."""
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


def make_openai_tool_call(
    name: str, arguments: dict, call_id: str = "call_001"
) -> dict:
    """Helper to build a tool_call dict matching OpenAI's format."""
    import json
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }
