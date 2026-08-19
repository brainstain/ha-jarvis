"""CalendarProvider protocol — all calendar backends satisfy this interface.

To add a new provider:
1. Create a new module in this package (e.g. ``google.py``, ``caldav.py``).
2. Implement the ``CalendarProvider`` protocol.
3. Register it in ``__init__.py`` ``get_provider()``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CalendarProvider(Protocol):
    """Minimal interface every calendar backend must implement."""

    async def list_calendars(self) -> list[dict[str, Any]]:
        """Return all accessible calendars.

        Each item must have at least: ``id``, ``name``.
        """
        ...

    async def list_events(
        self,
        calendar_id: str,
        start: str,
        end: str,
    ) -> list[dict[str, Any]]:
        """Return events in [start, end) (ISO-8601 strings).

        Each item must have at least: ``uid``, ``summary``, ``start``, ``end``.
        """
        ...

    async def create_event(
        self,
        calendar_id: str,
        summary: str,
        start: str,
        end: str,
        description: str = "",
        location: str = "",
    ) -> dict[str, Any]:
        """Create an event. Returns the created event dict or an error dict."""
        ...

    async def delete_event(
        self,
        calendar_id: str,
        uid: str,
    ) -> dict[str, Any]:
        """Delete an event by UID. Returns ``{\"deleted\": True}`` or error."""
        ...
