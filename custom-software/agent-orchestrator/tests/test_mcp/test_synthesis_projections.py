"""Per-tool synthesis field allowlists (agent.mcp.synthesis_projections)."""

from agent.mcp.synthesis_projections import project_for_synthesis


def test_calendar_events_keeps_only_allowlisted_fields():
    """The real shape calendar_events returns: a dict wrapping a list of
    event dicts, each carrying a uid/description/calendar_id the model has
    no use for -- and uid specifically is the confirmed live trigger for
    synthesis derailing into "should I mention this" instead of answering.
    """
    result = {
        "events": [
            {
                "uid": "abc123@google.com",
                "summary": "Becky's birthday",
                "start": "2026-09-18T07:00:00",
                "end": "2026-09-18T08:00:00",
                "description": "",
                "location": "",
                "all_day": False,
                "calendar_id": "primary",
            },
            {
                "uid": "def456@google.com",
                "summary": "Dentist",
                "start": "2026-09-18T14:00:00",
                "end": "2026-09-18T15:00:00",
                "description": "Annual checkup",
                "location": "123 Main St",
                "all_day": False,
                "calendar_id": "primary",
            },
        ],
        "count": 2,
    }

    projected = project_for_synthesis("mcp-calendar__calendar_events", result)

    assert projected == {
        "events": [
            {
                "summary": "Becky's birthday",
                "start": "2026-09-18T07:00:00",
                "end": "2026-09-18T08:00:00",
                "all_day": False,
            },
            {
                "summary": "Dentist",
                "start": "2026-09-18T14:00:00",
                "end": "2026-09-18T15:00:00",
                "location": "123 Main St",
                "all_day": False,
            },
        ],
        "count": 2,
    }


def test_unregistered_tool_falls_back_to_the_generic_denylist():
    """A tool with no SYNTHESIS_FIELDS entry gets trim_for_synthesis's
    denylist behavior instead -- no tool needs an allowlist on day one."""
    result = {"uid": "xyz", "state": "on", "note": ""}

    projected = project_for_synthesis("ha-mcp__light_turn_off", result)

    assert projected == {"state": "on"}
