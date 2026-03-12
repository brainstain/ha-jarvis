"""Conversation agent for HA Jarvis using Ollama or OpenAI-compatible APIs."""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

import aiohttp

from homeassistant.components import conversation
from homeassistant.components.conversation import trace
from homeassistant.components.conversation.const import HOME_ASSISTANT_AGENT
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent, llm
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    API_TYPE_OLLAMA,
    API_TYPE_OPENAI,
    CONF_API_KEY,
    CONF_API_TYPE,
    CONF_KEEP_ALIVE,
    CONF_MAX_HISTORY,
    CONF_MODEL,
    CONF_MODEL_NAME,
    CONF_PROMPT,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    CONF_TRY_HA_FIRST,
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
    MAX_TOOL_ITERATIONS,
    OLLAMA_CHAT_ENDPOINT,
    OPENAI_CHAT_ENDPOINT,
)

_LOGGER = logging.getLogger(__name__)

LLM_API_ID = "assist"


def _format_tool(tool: llm.Tool) -> dict[str, Any]:
    """Format an HA LLM tool into Ollama/OpenAI function-calling format."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.parameters,
        },
    }


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
        self._model = entry.data.get(CONF_MODEL, DEFAULT_MODEL)
        self._attr_unique_id = entry.entry_id
        self._conversation_history: dict[str, list[dict[str, Any]]] = {}

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
        """Get the API base URL."""
        return self.hass.data[DOMAIN][self.entry.entry_id]["base_url"]

    @property
    def _api_type(self) -> str:
        """Get the API type (ollama or openai)."""
        return self.entry.data.get(CONF_API_TYPE, DEFAULT_API_TYPE)

    @property
    def _api_key(self) -> str:
        """Get the API key for OpenAI-compatible endpoints."""
        return self.entry.data.get(CONF_API_KEY, DEFAULT_API_KEY)

    @property
    def _model_name(self) -> str:
        """Get the model name for OpenAI-compatible endpoints."""
        return self.entry.data.get(CONF_MODEL_NAME, DEFAULT_MODEL_NAME)

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
                 "fallback": self._api_type},
            )

        # Step 2: Fall back to LLM with HA tool support
        llm_api: llm.APIInstance | None = None
        try:
            llm_api = await llm.async_get_api(
                self.hass,
                LLM_API_ID,
                user_input.as_llm_context(DOMAIN),
            )
        except Exception as err:
            _LOGGER.warning("Could not load LLM API tools: %s", err)

        # Build tools list from HA's LLM API
        tools: list[dict[str, Any]] | None = None
        if llm_api and llm_api.tools:
            tools = [_format_tool(tool) for tool in llm_api.tools]

        # Build system prompt - combine our prompt with the LLM API prompt
        system_prompt = self._system_prompt
        if llm_api and llm_api.api_prompt:
            system_prompt = f"{system_prompt}\n\n{llm_api.api_prompt}"

        messages = self._build_messages(conversation_id, user_input.text, system_prompt)

        trace.async_conversation_trace_append(
            trace.ConversationTraceEventType.AGENT_DETAIL,
            {"messages": messages, "model": self._model,
             "tools_available": bool(tools), "api_type": self._api_type},
        )

        try:
            response_text = await self._call_llm_with_tools(
                messages, tools, llm_api, user_input
            )
        except Exception as err:
            _LOGGER.error("Error calling LLM (%s): %s", self._api_type, err)
            intent_response = intent.IntentResponse(language=user_input.language)
            intent_response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                f"Error communicating with LLM: {err}",
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
                "Error calling HA default agent, falling back to LLM: %s",
                err,
            )
            return None

    async def _call_llm_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        llm_api: llm.APIInstance | None,
        user_input: conversation.ConversationInput,
    ) -> str:
        """Call the configured LLM backend with tool support."""
        if self._api_type == API_TYPE_OPENAI:
            return await self._call_openai_with_tools(
                messages, tools, llm_api, user_input
            )
        return await self._call_ollama_with_tools(
            messages, tools, llm_api, user_input
        )

    # ------------------------------------------------------------------
    # Ollama backend
    # ------------------------------------------------------------------

    async def _call_ollama_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        llm_api: llm.APIInstance | None,
        user_input: conversation.ConversationInput,
    ) -> str:
        """Call Ollama with tool support, handling the tool-call loop."""
        for _iteration in range(MAX_TOOL_ITERATIONS):
            response = await self._call_ollama(messages, tools)

            message = response.get("message", {})
            tool_calls = message.get("tool_calls")

            if not tool_calls:
                return message.get("content", "")

            messages.append(message)

            for tool_call in tool_calls:
                func = tool_call.get("function", {})
                tool_name = func.get("name", "")
                tool_args = func.get("arguments", {})

                _LOGGER.debug("Ollama tool call: %s(%s)", tool_name, tool_args)

                trace.async_conversation_trace_append(
                    trace.ConversationTraceEventType.TOOL_CALL,
                    {"tool_name": tool_name, "tool_args": tool_args},
                )

                tool_result = await self._execute_tool(
                    tool_name, tool_args, llm_api, user_input
                )

                messages.append({
                    "role": "tool",
                    "content": json.dumps(tool_result),
                })

        _LOGGER.warning("Max tool iterations (%s) reached", MAX_TOOL_ITERATIONS)
        return await self._call_ollama_text_only(messages)

    async def _call_ollama(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Call the Ollama chat API, returning the full response dict."""
        url = f"{self._base_url}{OLLAMA_CHAT_ENDPOINT}"

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self._temperature,
                "top_p": self._top_p,
            },
            "keep_alive": self._keep_alive,
        }

        if tools:
            payload["tools"] = tools

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
                return await resp.json()

    async def _call_ollama_text_only(
        self, messages: list[dict[str, Any]]
    ) -> str:
        """Call Ollama without tools to get a final text response."""
        response = await self._call_ollama(messages, tools=None)
        return response.get("message", {}).get("content", "")

    # ------------------------------------------------------------------
    # OpenAI-compatible backend (LiteLLM, etc.)
    # ------------------------------------------------------------------

    async def _call_openai_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        llm_api: llm.APIInstance | None,
        user_input: conversation.ConversationInput,
    ) -> str:
        """Call OpenAI-compatible API with tool support."""
        for _iteration in range(MAX_TOOL_ITERATIONS):
            message = await self._call_openai(messages, tools)

            tool_calls = message.get("tool_calls")

            if not tool_calls:
                return message.get("content", "") or ""

            messages.append(message)

            for tool_call in tool_calls:
                func = tool_call.get("function", {})
                tool_name = func.get("name", "")
                tool_call_id = tool_call.get("id", "")

                # OpenAI returns arguments as a JSON string
                raw_args = func.get("arguments", "{}")
                if isinstance(raw_args, str):
                    try:
                        tool_args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        tool_args = {}
                else:
                    tool_args = raw_args

                _LOGGER.debug(
                    "OpenAI tool call: %s(%s) [id=%s]",
                    tool_name, tool_args, tool_call_id,
                )

                trace.async_conversation_trace_append(
                    trace.ConversationTraceEventType.TOOL_CALL,
                    {"tool_name": tool_name, "tool_args": tool_args},
                )

                tool_result = await self._execute_tool(
                    tool_name, tool_args, llm_api, user_input
                )

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps(tool_result),
                })

        _LOGGER.warning("Max tool iterations (%s) reached", MAX_TOOL_ITERATIONS)
        final = await self._call_openai(messages, tools=None)
        return final.get("content", "") or ""

    async def _call_openai(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Call an OpenAI-compatible chat completions API, returning the message dict."""
        url = f"{self._base_url}{OPENAI_CHAT_ENDPOINT}"

        payload: dict[str, Any] = {
            "model": self._model_name,
            "messages": messages,
            "stream": False,
            "temperature": self._temperature,
            "top_p": self._top_p,
        }

        if tools:
            payload["tools"] = tools

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise RuntimeError(
                        f"OpenAI API returned status {resp.status}: {error_text}"
                    )
                data = await resp.json()
                return data["choices"][0]["message"]

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    async def _execute_tool(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        llm_api: llm.APIInstance | None,
        user_input: conversation.ConversationInput,
    ) -> dict[str, Any]:
        """Execute a single HA tool call and return the result."""
        if llm_api is None:
            return {"error": "No LLM API available"}

        tool = next(
            (t for t in llm_api.tools if t.name == tool_name),
            None,
        )
        if tool is None:
            return {"error": f"Unknown tool: {tool_name}"}

        try:
            tool_input = llm.ToolInput(
                tool_name=tool_name,
                tool_args=tool_args,
            )
            result = await tool.async_call(
                self.hass,
                tool_input,
                llm_api.llm_context,
            )
            _LOGGER.debug("Tool %s result: %s", tool_name, result)
            return result
        except Exception as err:
            _LOGGER.error("Error executing tool %s: %s", tool_name, err)
            return {"error": str(err)}

    def _build_messages(
        self, conversation_id: str, user_text: str, system_prompt: str
    ) -> list[dict[str, Any]]:
        """Build the message list for the LLM."""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]

        if conversation_id in self._conversation_history:
            messages.extend(self._conversation_history[conversation_id])

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

        max_messages = self._max_history * 2
        if len(self._conversation_history[conversation_id]) > max_messages:
            self._conversation_history[conversation_id] = (
                self._conversation_history[conversation_id][-max_messages:]
            )
