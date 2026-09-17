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
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_TRY_HA_FIRST,
    DEFAULT_API_KEY,
    DEFAULT_BASE_URL,
    DEFAULT_TRY_HA_FIRST,
    DOMAIN,
    HEALTH_ENDPOINT,
)

_LOGGER = logging.getLogger(__name__)


async def _check_reachable(base_url: str) -> bool:
    """Check that the agent-orchestrator's HA health endpoint responds."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{base_url}{HEALTH_ENDPOINT}",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                return resp.status == 200
    except (aiohttp.ClientError, TimeoutError):
        return False


class HaJarvisConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HA Jarvis."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step - orchestrator connection details."""
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = user_input[CONF_BASE_URL].rstrip("/")

            if await _check_reachable(base_url):
                return self.async_create_entry(
                    title=f"Jarvis ({base_url})",
                    data={
                        CONF_BASE_URL: base_url,
                        CONF_API_KEY: user_input.get(CONF_API_KEY, DEFAULT_API_KEY),
                    },
                    options={
                        CONF_TRY_HA_FIRST: DEFAULT_TRY_HA_FIRST,
                    },
                )
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
                    vol.Optional(CONF_API_KEY, default=DEFAULT_API_KEY): str,
                }
            ),
            errors=errors,
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
                }
            ),
        )
