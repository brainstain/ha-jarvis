"""Conversation history — recent turns per thread_id, for multi-turn context.

SessionManager (core/session.py) decides which requests share a thread_id
(same voice satellite+speaker within SESSION_TIMEOUT, same webui
conversation_id, or an explicit thread_id). That resolution alone doesn't
give a graph any memory of what was said earlier on that thread — nothing
reads it back. This store is what actually carries prior turns forward so a
follow-up like "and the office one too" or "what about tomorrow?" can be
resolved against the previous exchange.

This is deliberately separate from the long-term semantic memory in
memory/scoping.py (facts recalled across sessions) and from LangGraph's
SQLite checkpointer (used only for interactive graph's within-flow HITL
pause/resume). It's a short rolling window of the literal last few turns.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class ConversationHistory:
    """In-memory rolling window of recent turns, keyed by thread_id."""

    max_turns: int = 8
    ttl_seconds: int = 1800
    _threads: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    _last_seen: dict[str, float] = field(default_factory=dict)

    def get_messages(self, thread_id: str) -> list[dict[str, str]]:
        """Prior turns for this thread, oldest first. Empty if unseen or expired.

        A thread with no new turns in ``ttl_seconds`` is treated as a lapsed
        conversation, not a continuation — its history is dropped rather
        than handed back.
        """
        now = time.monotonic()
        last = self._last_seen.get(thread_id)
        if last is None or (now - last) >= self.ttl_seconds:
            self._threads.pop(thread_id, None)
            self._last_seen.pop(thread_id, None)
            return []
        return list(self._threads.get(thread_id, []))

    def append(self, thread_id: str, role: str, content: str) -> None:
        """Record one turn, trimming to the most recent ``max_turns`` messages."""
        if not content:
            return
        turns = self._threads.setdefault(thread_id, [])
        turns.append({"role": role, "content": content})
        excess = max(0, len(turns) - self.max_turns)
        del turns[:excess]
        self._last_seen[thread_id] = time.monotonic()

    def expire(self) -> int:
        """Drop threads past their TTL. Returns the number expired."""
        now = time.monotonic()
        stale = [
            thread_id
            for thread_id, last in self._last_seen.items()
            if (now - last) >= self.ttl_seconds
        ]
        for thread_id in stale:
            self._threads.pop(thread_id, None)
            self._last_seen.pop(thread_id, None)
        return len(stale)
