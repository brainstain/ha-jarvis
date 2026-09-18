"""Per-tool field allowlists for synthesis prompts.

agent.core.llm.trim_for_synthesis is a generic denylist -- a fine default,
but it only ever knows to drop the noise patterns we've already seen (uid,
empty strings). As more tools get wired in, each can introduce its own
irrelevant-but-present fields, and patching one growing denylist for every
new tool doesn't scale.

This registry lets a tool opt into an explicit allowlist instead: only the
fields deliberately kept for synthesis, applied at any nesting depth (so a
tool's return shape can grow new internal detail -- extra IDs, tracking
metadata, whatever -- without it silently leaking into the prompt). A tool
with no entry here just falls back to trim_for_synthesis's denylist -- no
tool needs an entry on day one, only once it's actually shown to produce a
verbose or confusing synthesis prompt.

Note: an allowlist only helps with irrelevant *fields*. A tool whose result
is large because there's simply a lot of it (many search hits, a full page
of browser text) needs trimming at the source -- in that tool's own server
-- not a field filter here.
"""

from __future__ import annotations

from typing import Any

from agent.core.llm import filter_recursive, trim_for_synthesis

# Qualified tool name -> keys to keep, applied uniformly at every dict level
# in the result. Wrapper keys (e.g. calendar_events' "events"/"count") must
# be listed alongside the actual per-item fields, since the filter doesn't
# distinguish nesting depth.
SYNTHESIS_FIELDS: dict[str, set[str]] = {
    # mcp-calendar's calendar_events returns
    # {"events": [{uid, summary, start, end, description, location, all_day,
    # calendar_id}, ...], "count": N}. uid/description/calendar_id are never
    # useful in a spoken/text answer -- and uid specifically is the
    # confirmed live trigger for synthesis derailing into "should I mention
    # this" instead of answering (see trim_for_synthesis's docstring).
    "mcp-calendar__calendar_events": {
        "events",
        "count",
        "summary",
        "start",
        "end",
        "all_day",
        "location",
    },
}


def project_for_synthesis(tool_name: str, result: Any) -> Any:
    """Trim a tool's result for the synthesis prompt.

    Uses the tool's registered allowlist if one exists; otherwise falls
    back to the generic denylist (trim_for_synthesis).
    """
    allowed = SYNTHESIS_FIELDS.get(tool_name)
    if allowed is None:
        return trim_for_synthesis(result)
    return filter_recursive(result, keep=lambda k, v: k in allowed and v != "")
