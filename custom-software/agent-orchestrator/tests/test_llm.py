"""LLMClient response-cleanup: _strip_inline_reasoning."""

from agent.core.llm import _strip_inline_reasoning


def test_strips_closed_think_block():
    text = "<think>reasoning about the weather</think>It's sunny."
    assert _strip_inline_reasoning(text) == "It's sunny."


def test_empty_after_think_block_returns_original():
    text = "<think>only reasoning, nothing after</think>"
    assert _strip_inline_reasoning(text) == text


def test_multiline_json_with_no_think_tag_is_returned_whole():
    """Regression: grammar-constrained tool-selection can legitimately return
    complete JSON with no thinking at all, formatted across multiple lines.
    A calendar tool-selection call returned exactly this shape live and the
    old fallback ("no </think> means truncated, take the last line") shredded
    it down to a trailing '}', silently dropping the tool call.
    """
    text = (
        '{"tool": "mcp-calendar__calendar_events", '
        '"arguments": {"calendar_id": "primary", "start": "2025-01-20T00:00:00", '
        '"end": "2025-01-20T23:59:59"}\n}'
    )
    assert _strip_inline_reasoning(text) == text.strip()


def test_multiline_json_array_with_no_think_tag_is_returned_whole():
    text = '["a", "b"\n]'
    assert _strip_inline_reasoning(text) == text.strip()


def test_untagged_truncated_reasoning_falls_back_to_last_line():
    """A genuinely truncated free-text response (not JSON) still takes the
    last non-empty line — this fallback must keep working for synthesis."""
    text = "First I should consider the weather.\nThe answer is: sunny"
    assert _strip_inline_reasoning(text) == "The answer is: sunny"


def test_empty_text_returned_as_is():
    assert _strip_inline_reasoning("") == ""
