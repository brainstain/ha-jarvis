"""Tool-selection system prompt: current date must be injected."""

from datetime import UTC, datetime

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
