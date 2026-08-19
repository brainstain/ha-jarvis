"""MCP calendar server — read/write calendar events via HA or any provider.

The active provider is selected by CALENDAR_PROVIDER env var (default: "ha").
All date/time strings should be ISO-8601: 2025-01-15T14:00:00 or 2025-01-15.

Tools:
  calendar_list           — list available calendars
  calendar_events         — list events in a time window
  calendar_create_event   — create a new event
  calendar_delete_event   — delete an event by UID
"""

from __future__ import annotations

from typing import Any

import structlog
from fastmcp import FastMCP

from .providers import get_provider

log = structlog.get_logger(__name__)

mcp = FastMCP("mcp-calendar")


@mcp.tool()
async def calendar_list() -> dict[str, Any]:
    """List all accessible calendars.

    Returns a list of calendars with their IDs and names.
    Use the id (calendar_id) when calling other calendar tools.
    """
    provider = get_provider()
    try:
        calendars = await provider.list_calendars()
        return {"calendars": calendars}
    except Exception as exc:  # noqa: BLE001
        log.warning("calendar_list_failed", error=str(exc))
        return {"error": str(exc), "calendars": []}


@mcp.tool()
async def calendar_events(
    calendar_id: str,
    start: str,
    end: str,
) -> dict[str, Any]:
    """List events from a calendar within a time window.

    calendar_id: the calendar entity ID (from calendar_list)
    start: ISO-8601 start datetime, e.g. "2025-01-15T00:00:00"
    end:   ISO-8601 end datetime,   e.g. "2025-01-22T00:00:00"

    Returns a list of events with uid, summary, start, end, description, location.
    """
    provider = get_provider()
    try:
        events = await provider.list_events(calendar_id, start, end)
        return {"events": events, "count": len(events)}
    except Exception as exc:  # noqa: BLE001
        log.warning("calendar_events_failed", calendar_id=calendar_id, error=str(exc))
        return {"error": str(exc), "events": []}


@mcp.tool()
async def calendar_create_event(
    calendar_id: str,
    summary: str,
    start: str,
    end: str,
    description: str = "",
    location: str = "",
) -> dict[str, Any]:
    """Create a new calendar event.

    calendar_id:  the target calendar entity ID (from calendar_list)
    summary:      event title / name
    start:        ISO-8601 datetime, e.g. "2025-01-20T14:00:00"
    end:          ISO-8601 datetime, e.g. "2025-01-20T15:00:00"
    description:  optional longer description
    location:     optional location string

    For all-day events use date strings: "2025-01-20" (no time component).
    """
    provider = get_provider()
    try:
        result = await provider.create_event(
            calendar_id=calendar_id,
            summary=summary,
            start=start,
            end=end,
            description=description,
            location=location,
        )
        return result
    except Exception as exc:  # noqa: BLE001
        log.warning("calendar_create_failed", calendar_id=calendar_id, error=str(exc))
        return {"created": False, "error": str(exc)}


@mcp.tool()
async def calendar_delete_event(
    calendar_id: str,
    uid: str,
) -> dict[str, Any]:
    """Delete a calendar event by its UID.

    calendar_id: the calendar entity ID the event belongs to
    uid:         the event UID (from calendar_events results)
    """
    provider = get_provider()
    try:
        result = await provider.delete_event(calendar_id, uid)
        return result
    except Exception as exc:  # noqa: BLE001
        log.warning("calendar_delete_failed", calendar_id=calendar_id, uid=uid, error=str(exc))
        return {"deleted": False, "error": str(exc)}
