"""HACalendarProvider: real-vs-guessed calendar_id resolution."""

from __future__ import annotations

import httpx
import pytest

from mcp_calendar.providers import ha as ha_provider

CALENDARS = [
    {"entity_id": "calendar.family", "name": "Family"},
    {"entity_id": "calendar.michaelleegoldstein_gmail_com", "name": "Michael"},
]


def _events_response(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/calendars":
        return httpx.Response(200, json=CALENDARS)
    # /api/calendars/{entity_id}
    entity_id = request.url.path.rsplit("/", 1)[-1]
    return httpx.Response(
        200,
        json=[
            {
                "uid": f"evt-{entity_id}",
                "summary": f"Event on {entity_id}",
                "start": {"dateTime": "2026-09-17T10:00:00"},
                "end": {"dateTime": "2026-09-17T11:00:00"},
            }
        ],
    )


_RealAsyncClient = httpx.AsyncClient


@pytest.fixture
def provider(monkeypatch):
    transport = httpx.MockTransport(_events_response)

    def fake_client(*args, **kwargs):
        kwargs.pop("timeout", None)
        return _RealAsyncClient(transport=transport, base_url=ha_provider.HA_URL)

    monkeypatch.setattr(ha_provider.httpx, "AsyncClient", fake_client)
    return ha_provider.HACalendarProvider()


@pytest.mark.asyncio
async def test_real_calendar_id_queries_only_that_calendar(provider):
    events = await provider.list_events(
        "calendar.family", "2026-09-17T00:00:00", "2026-09-17T23:59:59"
    )
    assert len(events) == 1
    assert events[0]["calendar_id"] == "calendar.family"


@pytest.mark.asyncio
async def test_unrecognized_calendar_id_aggregates_all_calendars(provider):
    """Regression: an LLM-guessed calendar_id like "primary" (no prior turn
    to call list_calendars first) used to 400 straight through to the user.
    It should now transparently return events from every real calendar.
    """
    events = await provider.list_events(
        "primary", "2026-09-17T00:00:00", "2026-09-17T23:59:59"
    )
    assert {e["calendar_id"] for e in events} == {
        "calendar.family",
        "calendar.michaelleegoldstein_gmail_com",
    }
    assert len(events) == 2


@pytest.mark.asyncio
async def test_empty_calendar_id_also_aggregates(provider):
    events = await provider.list_events(
        "", "2026-09-17T00:00:00", "2026-09-17T23:59:59"
    )
    assert len(events) == 2
