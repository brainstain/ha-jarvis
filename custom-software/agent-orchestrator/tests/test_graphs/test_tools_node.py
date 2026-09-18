"""Tool-selection system prompt: current date must be injected."""

from datetime import UTC, datetime

from agent.config import get_settings
from agent.graphs.nodes.tools import _tool_selection_system


def test_includes_the_real_current_date():
    """Regression: without this, the model copied a tool docstring's
    example date verbatim for relative queries like "today" — every
    calendar query silently ran against 2025-01-20 instead of today.
    """
    prompt = _tool_selection_system()
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    assert today in prompt
    assert "2025-01-20" not in prompt


def test_still_carries_the_tool_selection_instruction():
    prompt = _tool_selection_system()
    assert "none" in prompt
    assert "single tool" in prompt


def test_instructs_omitting_time_max_for_next_event_queries():
    """Regression: confirmed live, the model filled in a narrow ~24h
    time_max for "next event" queries against google-workspace's
    get_events even though its docstring allows omitting it for an
    open-ended forward search — missing events further out.
    """
    prompt = _tool_selection_system()
    assert "time_max" in prompt
    assert "omit" in prompt.lower()


def test_includes_the_real_family_calendar_id_when_configured(monkeypatch):
    """Regression: confirmed live, the model named "Family" correctly from
    a prior list_calendars result but then either queried calendarId
    "primary" (finds nothing) or hallucinated the literal string "family"
    as calendarId (404s) — it needs the real ID handed to it directly.
    """
    monkeypatch.setenv("GOOGLE_CALENDAR_FAMILY_ID", "family123@group.calendar.google.com")
    get_settings.cache_clear()
    try:
        prompt = _tool_selection_system()
        assert "family123@group.calendar.google.com" in prompt
    finally:
        get_settings.cache_clear()


def test_omits_the_family_calendar_hint_when_not_configured(monkeypatch):
    monkeypatch.delenv("GOOGLE_CALENDAR_FAMILY_ID", raising=False)
    get_settings.cache_clear()
    try:
        prompt = _tool_selection_system()
        assert "family" not in prompt.lower()
    finally:
        get_settings.cache_clear()
