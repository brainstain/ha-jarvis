"""The HA Jarvis integration - bridges HA Assist to the agent-orchestrator."""

from __future__ import annotations

import logging

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    DEFAULT_API_KEY,
    DEFAULT_BASE_URL,
    DOMAIN,
    HEALTH_ENDPOINT,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CONVERSATION]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HA Jarvis from a config entry."""
    base_url = entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)

    # Verify agent-orchestrator is reachable
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{base_url}{HEALTH_ENDPOINT}",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    raise ConfigEntryNotReady(
                        f"agent-orchestrator returned status {resp.status}"
                    )
    except aiohttp.ClientError as err:
        raise ConfigEntryNotReady(
            f"Cannot connect to agent-orchestrator at {base_url}: {err}"
        ) from err

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "base_url": base_url,
        "api_key": entry.data.get(CONF_API_KEY, DEFAULT_API_KEY),
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
