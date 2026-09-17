"""Conversation agent for HA Jarvis.

Bridges Home Assistant's Assist pipeline to the agent-orchestrator's
POST /ha/conversation/process endpoint. Optionally tries HA's own
DefaultAgent (built-in intent matching) first, for fast local device
control without a network round trip; the orchestrator itself owns all
tool-calling (MCP servers, memory, calendar, etc.) for everything else.
"""

from __future__ import annotations

import logging
from typing import Literal

import aiohttp

from homeassistant.components import conversation
from homeassistant.components.conversation import trace
from homeassistant.components.conversation.const import HOME_ASSISTANT_AGENT
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONVERSATION_ENDPOINT, DEFAULT_TIMEOUT, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up conversation platform."""
    async_add_entities([JarvisConversationEntity(config_entry, hass)])


class JarvisConversationEntity(conversation.ConversationEntity):
    """HA Jarvis conversation agent entity."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    def __init__(self, entry: ConfigEntry, hass: HomeAssistant) -> None:
        """Initialize the entity."""
        self.entry = entry
        self.hass = hass
        self._attr_unique_id = entry.entry_id

    async def async_added_to_hass(self) -> None:
        """Register as a conversation agent when added to HA."""
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister as a conversation agent when removed."""
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return a list of supported languages."""
        return MATCH_ALL

    @property
    def _base_url(self) -> str:
        """Get the agent-orchestrator base URL."""
        return self.hass.data[DOMAIN][self.entry.entry_id]["base_url"]

    @property
    def _api_key(self) -> str:
        """Get the bearer token sent to the orchestrator."""
        return self.hass.data[DOMAIN][self.entry.entry_id]["api_key"]

    @property
    def _try_ha_first(self) -> bool:
        """Get try_ha_first setting."""
        return self.entry.options.get("try_ha_first", True)

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Process user input, trying HA built-in intents first if enabled."""
        conversation_id = user_input.conversation_id or "default"

        # Step 1: Try HA's built-in intent matching (DefaultAgent) first,
        # so simple device-control commands don't pay a network round trip.
        if self._try_ha_first:
            ha_result = await self._try_default_agent(user_input)
            if ha_result is not None:
                trace.async_conversation_trace_append(
                    trace.ConversationTraceEventType.AGENT_DETAIL,
                    {"source": "ha_default_agent", "matched": True},
                )
                return ha_result

            trace.async_conversation_trace_append(
                trace.ConversationTraceEventType.AGENT_DETAIL,
                {"source": "ha_default_agent", "matched": False,
                 "fallback": "agent_orchestrator"},
            )

        # Step 2: Forward everything else to the agent-orchestrator, which
        # owns memory, MCP tool-calling, and routing server-side.
        intent_response = intent.IntentResponse(language=user_input.language)

        try:
            speech, resolved_conversation_id = await self._call_orchestrator(
                user_input, conversation_id
            )
        except Exception as err:
            _LOGGER.error("Error calling agent-orchestrator: %s", err)
            intent_response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                f"Error communicating with the agent: {err}",
            )
            return conversation.ConversationResult(
                response=intent_response, conversation_id=conversation_id
            )

        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=user_input.agent_id,
                content=speech,
            )
        )

        intent_response.async_set_speech(speech)
        return conversation.ConversationResult(
            response=intent_response,
            conversation_id=resolved_conversation_id or conversation_id,
            continue_conversation=False,
        )

    async def _try_default_agent(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult | None:
        """Try HA's built-in DefaultAgent for intent matching.

        Returns the result if an intent was matched, or None if not.
        """
        try:
            result = await conversation.async_converse(
                hass=self.hass,
                text=user_input.text,
                conversation_id=None,
                context=user_input.context,
                language=user_input.language,
                agent_id=HOME_ASSISTANT_AGENT,
                device_id=user_input.device_id,
            )

            if (
                result.response.response_type
                != intent.IntentResponseType.ERROR
            ):
                _LOGGER.debug(
                    "HA default agent handled intent for: %s",
                    user_input.text,
                )
                return result

            _LOGGER.debug(
                "HA default agent did not match (error_code=%s): %s",
                result.response.error_code,
                user_input.text,
            )
            return None

        except Exception as err:
            _LOGGER.warning(
                "Error calling HA default agent, falling back to orchestrator: %s",
                err,
            )
            return None

    async def _call_orchestrator(
        self,
        user_input: conversation.ConversationInput,
        conversation_id: str,
    ) -> tuple[str, str]:
        """POST to agent-orchestrator's /ha/conversation/process.

        Returns (speech_text, conversation_id).
        """
        url = f"{self._base_url}{CONVERSATION_ENDPOINT}"
        payload = {
            "text": user_input.text,
            "language": user_input.language,
            "conversation_id": conversation_id,
            "agent_id": user_input.agent_id,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key or 'homeassistant'}",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise RuntimeError(
                        f"agent-orchestrator returned status {resp.status}: {error_text}"
                    )
                data = await resp.json()

        speech = data["response"]["speech"]["plain"]["speech"]
        resolved_conversation_id = data.get("conversation_id", conversation_id)
        return speech, resolved_conversation_id
