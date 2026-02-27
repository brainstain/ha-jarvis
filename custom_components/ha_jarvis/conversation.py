"""Conversation agent for HA Jarvis using Ollama."""

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

from .const import (
    CONF_KEEP_ALIVE,
    CONF_MAX_HISTORY,
    CONF_MODEL,
    CONF_PROMPT,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    CONF_TRY_HA_FIRST,
    DEFAULT_KEEP_ALIVE,
    DEFAULT_MAX_HISTORY,
    DEFAULT_MODEL,
    DEFAULT_PROMPT,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_P,
    DEFAULT_TRY_HA_FIRST,
    DOMAIN,
    OLLAMA_CHAT_ENDPOINT,
)

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

    def __init__(self, entry: ConfigEntry, hass: HomeAssistant) -> None:
        """Initialize the entity."""
        self.entry = entry
        self.hass = hass
        self._model = entry.data.get(CONF_MODEL, DEFAULT_MODEL)
        self._attr_unique_id = entry.entry_id
        self._conversation_history: dict[str, list[dict[str, str]]] = {}

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
        """Get the Ollama base URL."""
        return self.hass.data[DOMAIN][self.entry.entry_id]["base_url"]

    @property
    def _system_prompt(self) -> str:
        """Get the system prompt."""
        return self.entry.options.get(CONF_PROMPT, DEFAULT_PROMPT)

    @property
    def _max_history(self) -> int:
        """Get max conversation history turns."""
        return self.entry.options.get(CONF_MAX_HISTORY, DEFAULT_MAX_HISTORY)

    @property
    def _temperature(self) -> float:
        """Get temperature setting."""
        return self.entry.options.get(CONF_TEMPERATURE, DEFAULT_TEMPERATURE)

    @property
    def _top_p(self) -> float:
        """Get top_p setting."""
        return self.entry.options.get(CONF_TOP_P, DEFAULT_TOP_P)

    @property
    def _keep_alive(self) -> str:
        """Get keep_alive setting."""
        return self.entry.options.get(CONF_KEEP_ALIVE, DEFAULT_KEEP_ALIVE)

    @property
    def _try_ha_first(self) -> bool:
        """Get try_ha_first setting."""
        return self.entry.options.get(CONF_TRY_HA_FIRST, DEFAULT_TRY_HA_FIRST)

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Process user input, trying HA built-in intents first if enabled."""
        conversation_id = user_input.conversation_id or "default"

        # Step 1: Try HA's built-in intent matching (DefaultAgent) first
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
                 "fallback": "ollama"},
            )

        # Step 2: Fall back to Ollama LLM
        messages = self._build_messages(conversation_id, user_input.text)

        trace.async_conversation_trace_append(
            trace.ConversationTraceEventType.AGENT_DETAIL,
            {"messages": messages, "model": self._model},
        )

        try:
            response_text = await self._call_ollama(messages)
        except Exception as err:
            _LOGGER.error("Error calling Ollama: %s", err)
            intent_response = intent.IntentResponse(language=user_input.language)
            intent_response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                f"Error communicating with Ollama: {err}",
            )
            return conversation.ConversationResult(
                response=intent_response, conversation_id=conversation_id
            )

        # Store in our history
        self._update_history(conversation_id, user_input.text, response_text)

        # Add to HA chat log
        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=user_input.agent_id,
                content=response_text,
            )
        )

        intent_response = intent.IntentResponse(language=user_input.language)
        intent_response.async_set_speech(response_text)
        return conversation.ConversationResult(
            response=intent_response,
            conversation_id=conversation_id,
            continue_conversation=False,
        )

    async def _try_default_agent(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult | None:
        """Try HA's built-in DefaultAgent for intent matching.

        Returns the result if an intent was matched, or None if not.
        """
        try:
            # Call the built-in Home Assistant DefaultAgent
            result = await conversation.async_converse(
                hass=self.hass,
                text=user_input.text,
                conversation_id=None,  # Don't share conv history with default agent
                context=user_input.context,
                language=user_input.language,
                agent_id=HOME_ASSISTANT_AGENT,
                device_id=user_input.device_id,
            )

            # Check if the default agent actually matched an intent
            if (
                result.response.response_type
                != intent.IntentResponseType.ERROR
            ):
                _LOGGER.debug(
                    "HA default agent handled intent for: %s",
                    user_input.text,
                )
                return result

            # Default agent returned an error - check if it's a no-match
            _LOGGER.debug(
                "HA default agent did not match (error_code=%s): %s",
                result.response.error_code,
                user_input.text,
            )
            return None

        except Exception as err:
            _LOGGER.warning(
                "Error calling HA default agent, falling back to Ollama: %s",
                err,
            )
            return None

    def _build_messages(
        self, conversation_id: str, user_text: str
    ) -> list[dict[str, str]]:
        """Build the message list for Ollama."""
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self._system_prompt}
        ]

        # Add conversation history
        if conversation_id in self._conversation_history:
            messages.extend(self._conversation_history[conversation_id])

        # Add current user message
        messages.append({"role": "user", "content": user_text})

        return messages

    def _update_history(
        self, conversation_id: str, user_text: str, assistant_text: str
    ) -> None:
        """Update conversation history."""
        if conversation_id not in self._conversation_history:
            self._conversation_history[conversation_id] = []

        self._conversation_history[conversation_id].extend(
            [
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": assistant_text},
            ]
        )

        # Trim history (each turn = 2 messages)
        max_messages = self._max_history * 2
        if len(self._conversation_history[conversation_id]) > max_messages:
            self._conversation_history[conversation_id] = (
                self._conversation_history[conversation_id][-max_messages:]
            )

    async def _call_ollama(self, messages: list[dict[str, str]]) -> str:
        """Call the Ollama chat API."""
        url = f"{self._base_url}{OLLAMA_CHAT_ENDPOINT}"

        payload = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self._temperature,
                "top_p": self._top_p,
            },
            "keep_alive": self._keep_alive,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise RuntimeError(
                        f"Ollama returned status {resp.status}: {error_text}"
                    )
                data = await resp.json()
                return data["message"]["content"]
