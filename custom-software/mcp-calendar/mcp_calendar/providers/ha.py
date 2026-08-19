"""Home Assistant calendar provider.

Uses the HA REST API calendar endpoints:
  GET  /api/calendars                              → list calendar entities
  GET  /api/calendars/{entity_id}?start=&end=      → list events
  POST /api/calendars/{entity_id}/events           → create event
  DELETE /api/calendars/{entity_id}/events/{uid}   → delete event

HA exposes any calendar integration (Google Calendar, CalDAV, local, etc.)
through this unified REST interface, so this provider works with all of them.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx
import structlog

log = structlog.get_logger(__name__)

HA_URL = os.environ.get("HA_URL", "http://192.168.13.20:8123").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}


class HACalendarProvider:
    """Calendar provider backed by Home Assistant's calendar REST API."""

    async def list_calendars(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{HA_URL}/api/calendars", headers=_headers())
            resp.raise_for_status()
            raw: list[dict[str, Any]] = resp.json()

        return [
            {
                "id": cal.get("entity_id", ""),
                "name": cal.get("name", cal.get("entity_id", "")),
                "entity_id": cal.get("entity_id", ""),
            }
            for cal in raw
        ]

    async def list_events(
        self,
        calendar_id: str,
        start: str,
        end: str,
    ) -> list[dict[str, Any]]:
        url = f"{HA_URL}/api/calendars/{quote(calendar_id, safe='')}"
        params = {"start": start, "end": end}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=_headers(), params=params)
            resp.raise_for_status()
            raw: list[dict[str, Any]] = resp.json()

        events = []
        for ev in raw:
            events.append(
                {
                    "uid": ev.get("uid", ""),
                    "summary": ev.get("summary", ""),
                    "start": (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date", ""),
                    "end": (ev.get("end") or {}).get("dateTime") or (ev.get("end") or {}).get("date", ""),
                    "description": ev.get("description", ""),
                    "location": ev.get("location", ""),
                    "all_day": "dateTime" not in (ev.get("start") or {}),
                }
            )
        return events

    async def create_event(
        self,
        calendar_id: str,
        summary: str,
        start: str,
        end: str,
        description: str = "",
        location: str = "",
    ) -> dict[str, Any]:
        url = f"{HA_URL}/api/calendars/{quote(calendar_id, safe='')}/events"
        body: dict[str, Any] = {
            "summary": summary,
            "dtstart": start,
            "dtend": end,
        }
        if description:
            body["description"] = description
        if location:
            body["location"] = location

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=_headers(), json=body)
            if resp.status_code >= 400:
                return {"created": False, "error": resp.text[:300]}
            return {"created": True, **resp.json()} if resp.content else {"created": True}

    async def delete_event(
        self,
        calendar_id: str,
        uid: str,
    ) -> dict[str, Any]:
        url = f"{HA_URL}/api/calendars/{quote(calendar_id, safe='')}/events/{quote(uid, safe='')}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(url, headers=_headers())
            if resp.status_code >= 400:
                return {"deleted": False, "error": resp.text[:300]}
            return {"deleted": True}
