"""Shared test fixtures for ha_jarvis tests.

Mocks all homeassistant.* modules so the component can be imported without
a real HA installation.
"""

from __future__ import annotations

import sys
from types import ModuleType
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

# Install all stubs into sys.modules
sys.modules.update(_HA_MODULES)


# ---------------------------------------------------------------------------
# Now we can safely import the component
# ---------------------------------------------------------------------------
from custom_components.ha_jarvis.const import DOMAIN  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.data = {
        DOMAIN: {
            "test-entry-id": {
                "base_url": "http://192.168.13.22:8100",
                "api_key": "",
            }
        }
    }
    return hass


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry."""
    entry = MagicMock()
    entry.entry_id = "test-entry-id"
    entry.data = {
        "base_url": "http://192.168.13.22:8100",
        "api_key": "",
    }
    entry.options = {"try_ha_first": True}
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


def make_orchestrator_response(speech: str, conversation_id: str = "resolved-conv-id") -> dict:
    """Helper to build a mock /ha/conversation/process response."""
    return {
        "response": {
            "speech": {"plain": {"speech": speech, "extra_data": None}},
            "card": {},
            "language": "en",
            "response_type": "action_done",
        },
        "conversation_id": conversation_id,
    }
