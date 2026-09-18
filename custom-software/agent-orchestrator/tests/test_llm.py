"""LLMClient response-cleanup: _strip_inline_reasoning, trim_for_synthesis."""

from agent.core.llm import _strip_inline_reasoning, trim_for_synthesis


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


def test_trim_for_synthesis_drops_uid_and_empty_fields():
    """Regression: raw calendar events include a `uid` field with no use in
    a spoken/text answer, but its presence in the synthesis prompt has
    twice, live, provoked the model into deliberating out loud about
    whether to mention it ("But the user might not need the UIDs...")
    instead of answering — burning the tight token budget and truncating.
    """
    events = [
        {
            "uid": "abc123@google.com",
            "summary": "Becky's birthday",
            "start": "2026-09-18T07:00:00",
            "end": "2026-09-18T08:00:00",
            "description": "",
            "location": "",
            "all_day": False,
            "calendar_id": "primary",
        }
    ]
    assert trim_for_synthesis(events) == [
        {
            "summary": "Becky's birthday",
            "start": "2026-09-18T07:00:00",
            "end": "2026-09-18T08:00:00",
            "all_day": False,
            "calendar_id": "primary",
        }
    ]


def test_trim_for_synthesis_recurses_into_nested_structures():
    value = {"events": [{"uid": "x", "summary": "Bear", "location": ""}], "count": 1}
    assert trim_for_synthesis(value) == {"events": [{"summary": "Bear"}], "count": 1}


def test_trim_for_synthesis_passes_through_non_dict_values():
    assert trim_for_synthesis("light.kitchen is off") == "light.kitchen is off"
    assert trim_for_synthesis(None) is None
    assert trim_for_synthesis(42) == 42
