"""Config flow for HA Jarvis integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from .const import (
    CONF_HOST,
    CONF_KEEP_ALIVE,
    CONF_MAX_HISTORY,
    CONF_MODEL,
    CONF_PORT,
    CONF_PROMPT,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    CONF_TRY_HA_FIRST,
    DEFAULT_HOST,
    DEFAULT_KEEP_ALIVE,
    DEFAULT_MAX_HISTORY,
    DEFAULT_MODEL,
    DEFAULT_PORT,
    DEFAULT_PROMPT,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_P,
    DEFAULT_TRY_HA_FIRST,
    DOMAIN,
    OLLAMA_TAGS_ENDPOINT,
)

_LOGGER = logging.getLogger(__name__)


async def _fetch_models(host: str, port: int) -> list[str]:
    """Fetch available models from Ollama."""
    base_url = f"http://{host}:{port}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{base_url}{OLLAMA_TAGS_ENDPOINT}",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return [m["name"] for m in data.get("models", [])]
    except (aiohttp.ClientError, TimeoutError):
        pass
    return []


class HaJarvisConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HA Jarvis."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._host: str = DEFAULT_HOST
        self._port: int = DEFAULT_PORT
        self._available_models: list[str] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step - connection details."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._host = user_input[CONF_HOST]
            self._port = user_input[CONF_PORT]

            # Test connection
            base_url = f"http://{self._host}:{self._port}"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{base_url}{OLLAMA_TAGS_ENDPOINT}",
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status != 200:
                            errors["base"] = "cannot_connect"
                        else:
                            data = await resp.json()
                            self._available_models = [
                                m["name"] for m in data.get("models", [])
                            ]
                            if not self._available_models:
                                errors["base"] = "no_models"
                            else:
                                return await self.async_step_model()
            except (aiohttp.ClientError, TimeoutError):
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=self._host): str,
                    vol.Required(CONF_PORT, default=self._port): int,
                }
            ),
            errors=errors,
        )

    async def async_step_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle model selection step."""
        if user_input is not None:
            return self.async_create_entry(
                title=f"Jarvis ({user_input[CONF_MODEL]})",
                data={
                    CONF_HOST: self._host,
                    CONF_PORT: self._port,
                    CONF_MODEL: user_input[CONF_MODEL],
                },
                options={
                    CONF_TRY_HA_FIRST: DEFAULT_TRY_HA_FIRST,
                    CONF_PROMPT: DEFAULT_PROMPT,
                    CONF_MAX_HISTORY: DEFAULT_MAX_HISTORY,
                    CONF_KEEP_ALIVE: DEFAULT_KEEP_ALIVE,
                    CONF_TEMPERATURE: DEFAULT_TEMPERATURE,
                    CONF_TOP_P: DEFAULT_TOP_P,
                },
            )

        model_list = {m: m for m in self._available_models}

        return self.async_show_form(
            step_id="model",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MODEL, default=DEFAULT_MODEL): vol.In(
                        model_list
                    ),
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return HaJarvisOptionsFlow(config_entry)


class HaJarvisOptionsFlow(OptionsFlow):
    """Handle options for HA Jarvis."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self._config_entry.options

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_TRY_HA_FIRST,
                        default=options.get(
                            CONF_TRY_HA_FIRST, DEFAULT_TRY_HA_FIRST
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_PROMPT,
                        default=options.get(CONF_PROMPT, DEFAULT_PROMPT),
                    ): str,
                    vol.Optional(
                        CONF_MAX_HISTORY,
                        default=options.get(CONF_MAX_HISTORY, DEFAULT_MAX_HISTORY),
                    ): vol.All(int, vol.Range(min=0, max=100)),
                    vol.Optional(
                        CONF_TEMPERATURE,
                        default=options.get(CONF_TEMPERATURE, DEFAULT_TEMPERATURE),
                    ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=2.0)),
                    vol.Optional(
                        CONF_TOP_P,
                        default=options.get(CONF_TOP_P, DEFAULT_TOP_P),
                    ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
                    vol.Optional(
                        CONF_KEEP_ALIVE,
                        default=options.get(CONF_KEEP_ALIVE, DEFAULT_KEEP_ALIVE),
                    ): str,
                }
            ),
        )
