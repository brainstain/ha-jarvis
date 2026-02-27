"""The HA Jarvis integration - Local LLM conversation agent powered by Ollama."""

from __future__ import annotations

import logging

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_HOST,
    CONF_PORT,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DOMAIN,
    OLLAMA_TAGS_ENDPOINT,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CONVERSATION]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HA Jarvis from a config entry."""
    host = entry.data.get(CONF_HOST, DEFAULT_HOST)
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)
    base_url = f"http://{host}:{port}"

    # Verify Ollama is reachable
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{base_url}{OLLAMA_TAGS_ENDPOINT}",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    raise ConfigEntryNotReady(
                        f"Ollama server returned status {resp.status}"
                    )
    except aiohttp.ClientError as err:
        raise ConfigEntryNotReady(
            f"Cannot connect to Ollama at {base_url}: {err}"
        ) from err

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "base_url": base_url,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)
