"""Tool filtering — cap visible tools per request to reduce hallucination.

Research finding cited in SPEC.md: tool-call hallucination rises sharply above
roughly 7 visible tools, so the router picks categories and this module turns
those categories into a bounded, prioritized tool list.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

# Category -> MCP server(s) that provide it.
CATEGORY_SERVERS: dict[str, list[str]] = {
    "home_assistant": ["ha-mcp"],
    "memory": ["mcp-memory-scoped"],
    "calendar": ["mcp-calendar"],
    "search": ["mcp-searxng"],
    "documents": ["mcp-filesystem", "paperless"],
    "notifications": ["mcp-notifications"],
    "browser": ["mcp-playwright"],
    "filesystem": ["mcp-filesystem"],
}


@dataclass
class ToolSchema:
    """A single callable tool exposed by an MCP server."""

    name: str
    server: str
    category: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolRegistry:
    """All tools discovered across connected MCP servers."""

    tools: list[ToolSchema] = field(default_factory=list)
    _usage: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def register(self, tool: ToolSchema) -> None:
        self.tools.append(tool)

    def by_category(self, category: str) -> list[ToolSchema]:
        return [t for t in self.tools if t.category == category]

    def record_use(self, tool_name: str) -> None:
        """Track usage so hot tools win ties when trimming to the cap."""
        self._usage[tool_name] += 1

    def usage(self, tool_name: str) -> int:
        return self._usage.get(tool_name, 0)


class ToolFilter:
    """Selects at most `max_tools` schemas for a request."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def select_tools(self, categories: list[str], max_tools: int = 7) -> list[ToolSchema]:
        """Return the tools visible to the model for this request.

        Priority (SPEC.md):
          1. Earlier categories first — the router lists the most relevant first.
          2. Within a category, most-frequently-used tools first.
        """
        selected: list[ToolSchema] = []
        seen: set[str] = set()

        for category in categories:
            candidates = sorted(
                self.registry.by_category(category),
                key=lambda t: (-self.registry.usage(t.name), t.name),
            )
            for tool in candidates:
                if len(selected) >= max_tools:
                    return selected
                if tool.name in seen:
                    continue
                seen.add(tool.name)
                selected.append(tool)

        return selected
