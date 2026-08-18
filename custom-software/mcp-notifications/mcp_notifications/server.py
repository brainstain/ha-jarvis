"""Multi-channel notification delivery MCP server.

Delivers agent responses to users via four channels:
  notify_ha     — Home Assistant persistent notification or mobile app push
  notify_push   — ntfy.sh self-hosted push (phone/desktop)
  notify_webui  — Open WebUI message injection (POST to internal API)
  notify_tts    — HA TTS service (speaks result aloud on a media player)

Environment variables:
  HA_URL            http://gateway.home.local:8123
  HA_TOKEN          <PLACEHOLDER — see QUESTIONS.md>
  NTFY_URL          http://gateway.home.local:PORT/topic
                    (or https://ntfy.sh/your-topic — see QUESTIONS.md)
  WEBUI_URL         http://open-webui:8080
  WEBUI_API_KEY     sk-placeholder
  TTS_ENTITY        media_player.living_room   (default media player for TTS)
  TTS_PLATFORM      tts.google_translate_say   (HA TTS platform)
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog
from fastmcp import FastMCP

log = structlog.get_logger(__name__)

HA_URL = os.environ.get("HA_URL", "http://gateway.home.local:8123")
HA_TOKEN = os.environ.get("HA_TOKEN", "PLACEHOLDER_SEE_QUESTIONS_MD")
NTFY_URL = os.environ.get("NTFY_URL", "")                        # e.g. http://gateway:2586/jarvis
WEBUI_URL = os.environ.get("WEBUI_URL", "http://open-webui:8080")
WEBUI_API_KEY = os.environ.get("WEBUI_API_KEY", "sk-placeholder")
TTS_ENTITY = os.environ.get("TTS_ENTITY", "media_player.living_room")
TTS_PLATFORM = os.environ.get("TTS_PLATFORM", "tts.google_translate_say")

mcp = FastMCP("mcp-notifications")


def _ha_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json",
    }


# ──────────────────────────────────────────────────────────────────────
# Tools
# ──────────────────────────────────────────────────────────────────────


@mcp.tool()
async def notify_ha(
    message: str,
    title: str = "Jarvis",
    target: str = "persistent_notification",
) -> dict[str, Any]:
    """Send a notification via Home Assistant.

    target: HA service name under notify.* — e.g. "persistent_notification",
    "mobile_app_michael_iphone", "notify". Defaults to persistent_notification.
    """
    service = f"notify.{target}" if not target.startswith("notify.") else target
    domain, service_name = service.split(".", 1)
    url = f"{HA_URL}/api/services/{domain}/{service_name}"
    payload: dict[str, Any] = {"message": message, "title": title}

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, headers=_ha_headers(), json=payload)
        if resp.status_code >= 400:
            log.warning("notify_ha_failed", status=resp.status_code, body=resp.text[:200])
            return {"sent": False, "error": resp.text[:200]}
        return {"sent": True, "channel": "ha", "service": service}


@mcp.tool()
async def notify_push(
    message: str,
    title: str = "Jarvis",
    priority: str = "default",
    tags: list[str] | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Send a push notification via ntfy.

    Requires NTFY_URL env var. priority: min|low|default|high|urgent.
    tags are ntfy emoji tags (e.g. ["white_check_mark"]).
    """
    if not NTFY_URL:
        log.warning("ntfy_url_not_configured")
        return {"sent": False, "error": "NTFY_URL not configured — see QUESTIONS.md"}

    headers: dict[str, str] = {
        "Title": title,
        "Priority": priority,
    }
    if tags:
        headers["Tags"] = ",".join(tags)
    if user_id:
        headers["X-User-Id"] = user_id

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(NTFY_URL, content=message.encode(), headers=headers)
        if resp.status_code >= 400:
            return {"sent": False, "error": resp.text[:200]}
        return {"sent": True, "channel": "push", "url": NTFY_URL}


@mcp.tool()
async def notify_webui(
    message: str,
    user_id: str = "jarvis",
    channel_id: str | None = None,
) -> dict[str, Any]:
    """Inject a message into Open WebUI as an assistant response.

    This hits Open WebUI's internal REST API to post a message to a channel
    or conversation. Primarily used for async task results that arrive after
    the user's session has already responded.
    """
    # Open WebUI API: POST /api/v1/messages (channel messages)
    # or POST /api/v1/chats/{id}/messages
    url = f"{WEBUI_URL}/api/v1/messages"
    payload: dict[str, Any] = {
        "role": "assistant",
        "content": message,
        "channel_id": channel_id,
        "user_id": user_id,
    }
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
async def notify_tts(
    message: str,
    entity_id: str | None = None,
    language: str = "en",
) -> dict[str, Any]:
    """Speak a message aloud via Home Assistant TTS on a media player.

    entity_id: HA media_player entity (defaults to TTS_ENTITY env var).
    """
    target_entity = entity_id or TTS_ENTITY
    url = f"{HA_URL}/api/services/tts/speak"
    payload = {
        "entity_id": TTS_PLATFORM,
        "message": message,
        "language": language,
        "media_player_entity_id": target_entity,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, headers=_ha_headers(), json=payload)
        if resp.status_code >= 400:
            return {"sent": False, "error": resp.text[:200]}
        return {"sent": True, "channel": "tts", "entity": target_entity}
