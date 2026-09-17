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
