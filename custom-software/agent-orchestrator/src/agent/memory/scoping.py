"""Family/personal memory scope enforcement.

Every read and write passes through here so that scope boundaries are enforced
in one place rather than at each call site.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog

from agent.api.schemas import Memory, MemoryState

log = structlog.get_logger(__name__)

# Keywords that promote a personal memory to family scope (SPEC.md).
FAMILY_PROMOTION_PATTERNS = [
    r"\bfamily\b",
    r"\beveryone\b",
    r"\bwe(?:'re| are)\b",
    r"\bour\b",
    r"\bhousehold\b",
    r"\bgrocer(?:y|ies)\b",
    r"\bshopping list\b",
    r"\bcarpool\b",
    r"\bpick(?:ing)? up the kids\b",
    r"\bhome maintenance\b",
    r"\bhvac\b",
    r"\bfurnace\b",
    r"\bwater heater\b",
    r"\bappliance\b",
    r"\bvacation\b",
    r"\bdinner\b",
]
_FAMILY_RE = re.compile("|".join(FAMILY_PROMOTION_PATTERNS), re.IGNORECASE)


class VectorStore(Protocol):
    """Minimal surface the scoping layer needs from Qdrant."""

    async def search(
        self, query: str, scope_filter: dict[str, Any], limit: int
    ) -> list[dict[str, Any]]: ...

    async def upsert(self, memory_id: str, text: str, payload: dict[str, Any]) -> str: ...

    async def set_payload(self, memory_id: str, payload: dict[str, Any]) -> bool: ...


class ScopedMemory:
    """Enforces family/personal memory boundaries at every access point."""

    def __init__(self, store: VectorStore, auto_promote_family: bool = True) -> None:
        self.store = store
        self.auto_promote_family = auto_promote_family

    # ── filters ──────────────────────────────────────────────
    @staticmethod
    def build_filter(user_id: str, scope: str) -> dict[str, Any]:
        """Scope rules from SPEC.md.

        family   -> only family memories
        personal -> family memories OR this user's personal memories
        """
        if scope == "family":
            return {"must": [{"key": "scope", "match": {"value": "family"}}]}
        return {
            "should": [
                {"must": [{"key": "scope", "match": {"value": "family"}}]},
                {
                    "must": [
                        {"key": "scope", "match": {"value": "personal"}},
                        {"key": "user_id", "match": {"value": user_id}},
                    ]
                },
            ]
        }

    # ── operations ───────────────────────────────────────────
    async def search(
        self, query: str, user_id: str, scope: str, limit: int = 5
    ) -> list[Memory]:
        """Search memories visible to this user at this scope."""
        raw = await self.store.search(query, self.build_filter(user_id, scope), limit)
        results: list[Memory] = []
        for hit in raw:
            payload = hit.get("payload", hit)
            results.append(
                Memory(
                    id=str(hit.get("id") or payload.get("id") or ""),
                    text=payload.get("text", ""),
                    user_id=payload.get("user_id", user_id),
                    scope=payload.get("scope", "personal"),
                    memory_type=payload.get("memory_type", "fact"),
                    state=MemoryState(payload.get("state", MemoryState.CONFIRMED)),
                    tags=payload.get("tags", []),
                    source=payload.get("source", "conversation"),
                )
            )
        return results

    def should_promote(self, text: str) -> bool:
        """True when a personal memory looks like shared household context."""
        return bool(self.auto_promote_family and _FAMILY_RE.search(text))

    async def store_memory(self, memory: Memory, user_id: str, scope: str) -> str:
        """Persist a memory with full scope metadata."""
        effective_scope = scope
        promoted = False
        if scope == "personal" and self.should_promote(memory.text):
            effective_scope = "family"
            promoted = True

        memory_id = memory.id or str(uuid.uuid4())
        payload = {
            "text": memory.text,
            "scope": effective_scope,
            "user_id": user_id,
            "memory_type": memory.memory_type,
            "state": str(memory.state),
            "timestamp": (memory.timestamp or datetime.now(UTC)).isoformat(),
            "tags": memory.tags,
            "source": memory.source,
            "auto_promoted": promoted,
        }
        stored_id = await self.store.upsert(memory_id, memory.text, payload)
        if promoted:
            log.info("memory_auto_promoted", memory_id=stored_id, user_id=user_id)
        return stored_id

    async def update_state(self, memory_id: str, new_state: MemoryState) -> bool:
        """Transition a memory's lifecycle state (pending -> confirmed, etc.)."""
        return await self.store.set_payload(memory_id, {"state": str(new_state)})
