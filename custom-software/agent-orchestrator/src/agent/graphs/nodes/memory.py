"""Memory lookup node factory."""

from __future__ import annotations

from typing import Any, Callable

import structlog

from agent.memory.scoping import ScopedMemory

log = structlog.get_logger(__name__)


def make_memory_lookup(memory: ScopedMemory) -> Callable[[dict[str, Any]], Any]:
    """Return an async callable that fetches relevant memories for the current state."""

    async def lookup(state: dict[str, Any]) -> list[dict[str, Any]]:
        user_id = state.get("user_id", "")
        scope = state.get("scope", "personal")
        query = state.get("message", "")
        if not query or not user_id:
            return []
        try:
            hits = await memory.search(query, user_id, scope, limit=5)
            return [
                {
                    "text": m.text,
                    "scope": m.scope,
                    "type": m.memory_type,
                    "tags": m.tags,
                }
                for m in hits
            ]
        except Exception as exc:  # noqa: BLE001
            log.warning("memory_lookup_failed", error=str(exc))
            return []

    return lookup
