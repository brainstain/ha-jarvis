"""Multi-channel notification delivery MCP server.

All delivery channels go through Home Assistant — no third-party services
required. HA handles mobile push via the Companion app, persistent UI
notifications, and TTS on any registered media player.

Channels:
  notify_user    — push to all of a user's HA mobile devices (discovered dynamically)
  notify_all     — broadcast persistent notification visible to everyone in HA
  notify_tts     — speak on any HA media player, optionally filtered by area/room
  notify_webui   — inject a message into Open WebUI for a specific conversation

Environment variables (passed via docker-compose, sourced from .env):
  HA_URL          http://192.168.13.20:8123
  HA_TOKEN        long-lived access token
  WEBUI_URL       http://open-webui:8080
  WEBUI_API_KEY   from Open WebUI admin panel
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog
from fastmcp import FastMCP

log = structlog.get_logger(__name__)

HA_URL = os.environ.get("HA_URL", "http://192.168.13.20:8123").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
WEBUI_URL = os.environ.get("WEBUI_URL", "http://open-webui:8080").rstrip("/")
WEBUI_API_KEY = os.environ.get("WEBUI_API_KEY", "sk-placeholder")

mcp = FastMCP("mcp-notifications")


def _ha_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}


async def _ha_get(path: str) -> Any:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{HA_URL}/api{path}", headers=_ha_headers())
        resp.raise_for_status()
        return resp.json()


async def _ha_post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(f"{HA_URL}/api{path}", headers=_ha_headers(), json=body)
        if resp.status_code >= 400:
            return {"error": resp.text[:300], "sent": False}
        return {"sent": True}


# ──────────────────────────────────────────────────────────────────────
# Discovery helpers
# ──────────────────────────────────────────────────────────────────────


async def _discover_notify_services() -> list[str]:
    """Return all available notify.* service names from HA."""
    try:
        services = await _ha_get("/services")
        notify = next((s for s in services if s.get("domain") == "notify"), {})
        return list((notify.get("services") or {}).keys())
    except Exception as exc:  # noqa: BLE001
        log.warning("notify_service_discovery_failed", error=str(exc))
        return []


async def _discover_media_players(area: str | None = None) -> list[dict[str, Any]]:
    """Return media_player entities, optionally filtered by area name."""
    try:
        states = await _ha_get("/states")
        players = [
            s for s in states
            if s.get("entity_id", "").startswith("media_player.")
            and s.get("state") not in ("unavailable", "unknown")
        ]
        if area:
            area_lower = area.lower()
            players = [
                p for p in players
                if area_lower in (p.get("attributes", {}).get("friendly_name", "")).lower()
                or area_lower in str(p.get("attributes", {}).get("area_id", "")).lower()
            ]
        return players
    except Exception as exc:  # noqa: BLE001
        log.warning("media_player_discovery_failed", error=str(exc))
        return []


async def _discover_tts_entities() -> list[str]:
    """Return TTS provider entity IDs from HA."""
    try:
        states = await _ha_get("/states")
        return [
            s["entity_id"]
            for s in states
            if s.get("entity_id", "").startswith("tts.")
        ]
    except Exception as exc:  # noqa: BLE001
        log.warning("tts_discovery_failed", error=str(exc))
        return ["tts.google_translate_say"]  # safe default


# ──────────────────────────────────────────────────────────────────────
# Tools
# ──────────────────────────────────────────────────────────────────────


@mcp.tool()
async def notify_user(
    message: str,
    user_id: str,
    title: str = "Jarvis",
) -> dict[str, Any]:
    """Push a notification to a specific user's mobile devices via HA Companion app.

    Discovers all notify.mobile_app_* services whose name contains user_id,
    and sends to all of them. Falls back to persistent_notification if no
    mobile services are found for that user.

    user_id: the person's name or ID as it appears in their HA mobile app
    service name (e.g. "michael" for "notify.mobile_app_michael_iphone").
    """
    services = await _discover_notify_services()
    user_lower = user_id.lower().replace(" ", "_")
    mobile_services = [
        s for s in services
        if "mobile_app" in s and user_lower in s.lower()
    ]

    if not mobile_services:
        log.info("no_mobile_services_found", user_id=user_id, falling_back="persistent_notification")
        return await _ha_post(
            "/services/persistent_notification/create",
            {"message": message, "title": title},
        )

    results = []
    for svc in mobile_services:
        result = await _ha_post(
            f"/services/notify/{svc}",
            {"message": message, "title": title},
        )
        results.append({"service": svc, **result})

    sent = all(r.get("sent") for r in results)
    return {"sent": sent, "targets": results}


@mcp.tool()
async def notify_all(
    message: str,
    title: str = "Jarvis",
) -> dict[str, Any]:
    """Broadcast a persistent notification visible to all users in HA."""
    return await _ha_post(
        "/services/persistent_notification/create",
        {"message": message, "title": title, "notification_id": "jarvis_broadcast"},
    )


@mcp.tool()
async def notify_tts(
    message: str,
    area: str | None = None,
    entity_id: str | None = None,
) -> dict[str, Any]:
    """Speak a message aloud via HA TTS on one or more media players.

    If entity_id is given, speaks only on that player.
    If area is given, speaks on all media players in that room.
    If neither is given, speaks on ALL available media players.

    Uses HA's tts.speak service with the first available TTS provider.
    """
    if entity_id:
        targets = [entity_id]
    else:
        players = await _discover_media_players(area=area)
        targets = [p["entity_id"] for p in players]

    if not targets:
        return {"sent": False, "error": "No media players found"}

    tts_entities = await _discover_tts_entities()
    tts_provider = tts_entities[0] if tts_entities else "tts.google_translate_say"

    results = []
    for target in targets:
        result = await _ha_post(
            "/services/tts/speak",
            {
                "entity_id": tts_provider,
                "media_player_entity_id": target,
                "message": message,
            },
        )
        results.append({"entity": target, **result})

    sent = any(r.get("sent") for r in results)
    return {"sent": sent, "targets": results}


@mcp.tool()
async def notify_webui(
    message: str,
    channel_id: str | None = None,
) -> dict[str, Any]:
    """Inject an assistant message into an Open WebUI channel or chat.

    Used for async task results that arrive after the original WebSocket
    session has closed. channel_id is the Open WebUI chat/channel UUID.
    """
    url = f"{WEBUI_URL}/api/v1/messages"
    payload: dict[str, Any] = {
        "role": "assistant",
        "content": message,
    }
    if channel_id:
        payload["channel_id"] = channel_id

    headers = {
        "Authorization": f"Bearer {WEBUI_API_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            return {"sent": False, "error": resp.text[:200]}
        return {"sent": True, "channel": "webui"}


@mcp.tool()
async def list_notify_targets() -> dict[str, Any]:
    """List available notification targets for routing decisions.

    Returns mobile devices (per-user), media players (with area), and TTS providers.
    The LLM uses this to choose the right channel for a given context.
    """
    services = await _discover_notify_services()
    mobile = [s for s in services if "mobile_app" in s]
    players = await _discover_media_players()
    tts = await _discover_tts_entities()

    return {
        "mobile_services": mobile,
        "media_players": [
            {
                "entity_id": p["entity_id"],
                "name": p.get("attributes", {}).get("friendly_name", p["entity_id"]),
                "state": p.get("state"),
            }
            for p in players
        ],
        "tts_providers": tts,
    }
