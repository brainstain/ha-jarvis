"""Session manager — maps (source, user_id, context) to LangGraph thread_ids."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from agent.api.schemas import ChatRequest, ThreadInfo


@dataclass
class _Session:
    thread_id: str
    last_seen: float
    user_id: str


@dataclass
class SessionManager:
    """Resolves or creates thread_ids with TTL-based expiry.

    Resolution rules (SPEC.md):
      1. Explicit request.thread_id wins.
      2. voice: same satellite + speaker within SESSION_TIMEOUT -> reuse.
      3. webui: same Open WebUI conversation (metadata.conversation_id) -> reuse.
      4. Otherwise a fresh uuid4 thread.
    """

    session_timeout_seconds: int = 300
    _sessions: dict[str, _Session] = field(default_factory=dict)
    # thread_id -> pending HITL question
    _pending: dict[str, ThreadInfo] = field(default_factory=dict)

    # ── key construction ─────────────────────────────────────
    @staticmethod
    def _voice_key(satellite_id: str | None, speaker_id: str | None, user_id: str) -> str:
        speaker = speaker_id or user_id
        return f"voice:{satellite_id or 'unknown'}:{speaker}"

    @staticmethod
    def _webui_key(conversation_id: str, user_id: str) -> str:
        return f"webui:{user_id}:{conversation_id}"

    def _session_key(self, request: ChatRequest) -> str | None:
        if request.source == "voice":
            return self._voice_key(request.satellite_id, request.speaker_id, request.user_id)
        if request.source == "webui":
            conversation_id = (request.metadata or {}).get("conversation_id")
            if conversation_id:
                return self._webui_key(str(conversation_id), request.user_id)
        return None

    # ── public API ───────────────────────────────────────────
    def resolve_thread(self, request: ChatRequest) -> str:
        """Return the thread_id this request should run on."""
        if request.thread_id:
            return request.thread_id

        key = self._session_key(request)
        now = time.monotonic()

        if key is not None:
            session = self._sessions.get(key)
            if session and (now - session.last_seen) < self.session_timeout_seconds:
                session.last_seen = now
                return session.thread_id

        thread_id = str(uuid.uuid4())
        if key is not None:
            self._sessions[key] = _Session(
                thread_id=thread_id, last_seen=now, user_id=request.user_id
            )
        return thread_id

    def register_pending(self, thread_id: str, info: ThreadInfo) -> None:
        """Record a thread that interrupted for human input."""
        self._pending[thread_id] = info

    def clear_pending(self, thread_id: str) -> None:
        self._pending.pop(thread_id, None)

    def get_pending_threads(self, user_id: str) -> list[ThreadInfo]:
        """Threads with HITL interrupts awaiting this user."""
        return [info for info in self._pending.values() if info.user_id == user_id]

    def expire_sessions(self) -> int:
        """Drop sessions past their TTL. Returns the number expired."""
        now = time.monotonic()
        stale = [
            key
            for key, session in self._sessions.items()
            if (now - session.last_seen) >= self.session_timeout_seconds
        ]
        for key in stale:
            del self._sessions[key]
        return len(stale)
