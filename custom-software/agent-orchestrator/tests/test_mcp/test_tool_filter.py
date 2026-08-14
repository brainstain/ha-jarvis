"""Tool filtering: max-7 cap and priority ordering."""

from agent.mcp.tool_filter import ToolFilter, ToolRegistry, ToolSchema


def registry_with(counts: dict[str, int]) -> ToolRegistry:
    registry = ToolRegistry()
    for category, n in counts.items():
        for i in range(n):
            registry.register(
                ToolSchema(name=f"{category}.tool{i}", server=f"{category}-mcp", category=category)
            )
    return registry


def test_caps_at_seven_tools():
    registry = registry_with({"home_assistant": 10})
    selected = ToolFilter(registry).select_tools(["home_assistant"], max_tools=7)
    assert len(selected) == 7


def test_first_category_prioritized():
    registry = registry_with({"home_assistant": 5, "calendar": 5})
    selected = ToolFilter(registry).select_tools(["home_assistant", "calendar"], max_tools=7)
    categories = [t.category for t in selected]
    assert categories[:5] == ["home_assistant"] * 5
    assert categories[5:] == ["calendar"] * 2


def test_frequently_used_tools_win_within_category():
    registry = registry_with({"memory": 3})
    registry.record_use("memory.tool2")
    registry.record_use("memory.tool2")
    selected = ToolFilter(registry).select_tools(["memory"], max_tools=1)
    assert selected[0].name == "memory.tool2"


def test_no_duplicates_across_overlapping_categories():
    registry = ToolRegistry()
    shared = ToolSchema(name="fs.read", server="mcp-filesystem", category="filesystem")
    registry.register(shared)
    registry.register(shared)
    selected = ToolFilter(registry).select_tools(["filesystem", "filesystem"], max_tools=7)
    assert len(selected) == 1


def test_unknown_category_yields_nothing():
    registry = registry_with({"memory": 2})
    assert ToolFilter(registry).select_tools(["nonexistent"]) == []
