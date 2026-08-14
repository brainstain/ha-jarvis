"""Session manager: thread resolution and expiry."""

from datetime import UTC, datetime

from agent.api.schemas import ChatRequest, ThreadInfo
from agent.core.session import SessionManager


def voice_request(**overrides) -> ChatRequest:
    base = dict(
        message="turn off the lights",
        user_id="michael",
        scope="family",
        source="voice",
        satellite_id="kitchen",
        speaker_id="michael",
    )
    base.update(overrides)
    return ChatRequest(**base)


def test_explicit_thread_id_wins():
    manager = SessionManager()
    request = voice_request(thread_id="explicit-123")
    assert manager.resolve_thread(request) == "explicit-123"


def test_voice_same_satellite_and_speaker_reuses_thread():
    manager = SessionManager(session_timeout_seconds=300)
    first = manager.resolve_thread(voice_request())
    second = manager.resolve_thread(voice_request(message="and the fan"))
    assert first == second


def test_voice_different_satellite_gets_new_thread():
    manager = SessionManager()
    kitchen = manager.resolve_thread(voice_request(satellite_id="kitchen"))
    office = manager.resolve_thread(voice_request(satellite_id="office"))
    assert kitchen != office


def test_voice_different_speaker_gets_new_thread():
    manager = SessionManager()
    mike = manager.resolve_thread(voice_request(speaker_id="michael"))
    other = manager.resolve_thread(voice_request(speaker_id="guest"))
    assert mike != other


def test_expired_voice_session_gets_new_thread():
    manager = SessionManager(session_timeout_seconds=0)
    first = manager.resolve_thread(voice_request())
    second = manager.resolve_thread(voice_request())
    assert first != second


def test_webui_conversation_id_reuses_thread():
    manager = SessionManager()
    request = ChatRequest(
        message="hi",
        user_id="michael",
        scope="personal",
        source="webui",
        metadata={"conversation_id": "abc"},
    )
    assert manager.resolve_thread(request) == manager.resolve_thread(request)


def test_webui_without_conversation_id_is_always_new():
    manager = SessionManager()
    request = ChatRequest(message="hi", user_id="michael", scope="personal", source="webui")
    assert manager.resolve_thread(request) != manager.resolve_thread(request)


def test_expire_sessions_removes_stale_entries():
    manager = SessionManager(session_timeout_seconds=0)
    manager.resolve_thread(voice_request())
    assert manager.expire_sessions() == 1
    assert manager.expire_sessions() == 0


def test_pending_threads_filtered_by_user():
    manager = SessionManager()
    now = datetime.now(UTC)
    manager.register_pending(
        "t1", ThreadInfo(thread_id="t1", user_id="michael", question="Which room?", created_at=now)
    )
    manager.register_pending(
        "t2", ThreadInfo(thread_id="t2", user_id="other", question="Which one?", created_at=now)
    )
    pending = manager.get_pending_threads("michael")
    assert [info.thread_id for info in pending] == ["t1"]

    manager.clear_pending("t1")
    assert manager.get_pending_threads("michael") == []
