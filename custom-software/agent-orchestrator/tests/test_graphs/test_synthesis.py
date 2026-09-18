"""make_synthesizer: multistep/interactive response synthesis hardening.

Regression coverage for a bug where a calendar lookup routed to the
multistep graph could return raw truncated qwen3 reasoning straight to the
user (WebUI: "...and Bear (") or silently fall back to "Done." with no
recap (Home Assistant) — because this synthesizer, unlike routes.py's
_run_simple synthesizer, called the LLM with default thinking enabled and
had no check for truncated/reasoning-shaped output before returning it.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from agent.graphs.nodes.synthesis import make_synthesizer


class FakeLLM:
    def __init__(self, content: str, synthesis_max_tokens: int = 300):
        self.content = content
        self.settings = SimpleNamespace(synthesis_max_tokens=synthesis_max_tokens)
        self.calls: list[dict] = []

    async def complete(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return {"role": "assistant", "content": self.content}


def _state(with_tool_result=True):
    tool_calls = (
        [{"tool": "mcp-calendar__list_events", "result": {"events": ["Becky's birthday"]}}]
        if with_tool_result
        else []
    )
    return {"message": "what's on the calendar tomorrow", "tool_calls": tool_calls}


@pytest.mark.asyncio
async def test_disables_thinking_and_uses_synthesis_budget():
    llm = FakeLLM("Tomorrow you have Becky's birthday.")
    synthesize = make_synthesizer(llm)

    await synthesize(_state())

    assert llm.calls[0]["extra_body"] == {"think": False}
    assert llm.calls[0]["max_tokens"] == 300


@pytest.mark.asyncio
async def test_synthesis_system_prompt_carries_the_real_date():
    """Regression: the tool call resolves "today"/"tomorrow" against the
    real date (graphs.nodes.tools._tool_selection_system), but this
    synthesizer's system prompt had no date of its own — synthesis, reading
    only the tool's raw ISO timestamps back, had nothing to anchor "today"
    to and started guessing the year instead of just reading the result.
    """
    llm = FakeLLM("Tomorrow you have Becky's birthday.")
    synthesize = make_synthesizer(llm)

    await synthesize(_state())

    system_prompt = llm.calls[0]["messages"][0]["content"]
    assert datetime.now(UTC).strftime("%Y-%m-%d") in system_prompt


@pytest.mark.asyncio
async def test_synthesis_prompt_omits_uid_from_tool_result():
    """Regression: calendar events carry a `uid` field with no use in a
    spoken/text answer, but its presence in the raw tool-result JSON has
    twice, live, provoked the model into deliberating out loud about
    whether to mention it instead of just answering, truncating before it
    ever got there. The synthesis prompt must not show the model a uid.
    """
    state = {
        "message": "what's on the calendar tomorrow",
        "tool_calls": [
            {
                "tool": "mcp-calendar__list_events",
                "result": [
                    {
                        "uid": "abc123@google.com",
                        "summary": "Becky's birthday",
                        "start": "2026-09-18T07:00:00",
                        "end": "2026-09-18T08:00:00",
                    }
                ],
            }
        ],
    }
    llm = FakeLLM("Tomorrow you have Becky's birthday.")
    synthesize = make_synthesizer(llm)

    await synthesize(state)

    tool_result_message = llm.calls[0]["messages"][-1]["content"]
    assert "uid" not in tool_result_message
    assert "abc123@google.com" not in tool_result_message
    assert "Becky's birthday" in tool_result_message


@pytest.mark.asyncio
async def test_truncated_inline_reasoning_falls_back_instead_of_leaking():
    """The exact shape of the bug: mid-thought text with no closing
    punctuation, cut off by max_tokens before the model ever answered."""
    llm = FakeLLM(
        "But the user might not need the UIDs or other details. So the summary "
        "is Becky's birthday (7-8 AM), Wear red and black (12-1 PM), and Bear ("
    )
    synthesize = make_synthesizer(llm)

    result = await synthesize(_state())

    assert result["response"] == "Done."
    assert result["confidence"] <= 0.3


@pytest.mark.asyncio
async def test_reasoning_keyword_prefix_falls_back_even_when_long():
    llm = FakeLLM(
        "Let me reconsider what the user actually wants here before I give "
        "a final answer about their calendar for tomorrow."
    )
    synthesize = make_synthesizer(llm)

    result = await synthesize(_state())

    assert result["response"] == "Done."


@pytest.mark.asyncio
async def test_well_formed_answer_passes_through_unchanged():
    llm = FakeLLM("Tomorrow you have Becky's birthday and a 12 PM reminder.")
    synthesize = make_synthesizer(llm)

    result = await synthesize(_state())

    assert result["response"] == "Tomorrow you have Becky's birthday and a 12 PM reminder."
    assert result["confidence"] == 0.9


@pytest.mark.asyncio
async def test_empty_reply_without_tool_result_uses_error_fallback():
    llm = FakeLLM("")
    synthesize = make_synthesizer(llm)

    result = await synthesize(_state(with_tool_result=False))

    assert result["response"] == "I couldn't complete that — please try again."
