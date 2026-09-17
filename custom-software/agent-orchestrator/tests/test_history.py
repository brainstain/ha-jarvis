"""Conversation history: rolling per-thread turn window and expiry."""

from agent.core.history import ConversationHistory


def test_new_thread_has_no_history():
    history = ConversationHistory()
    assert history.get_messages("t1") == []


def test_append_then_get_returns_turns_in_order():
    history = ConversationHistory()
    history.append("t1", "user", "turn off the kitchen light")
    history.append("t1", "assistant", "Kitchen light is off.")

    assert history.get_messages("t1") == [
        {"role": "user", "content": "turn off the kitchen light"},
        {"role": "assistant", "content": "Kitchen light is off."},
    ]


def test_threads_are_isolated():
    history = ConversationHistory()
    history.append("t1", "user", "hi from thread one")
    history.append("t2", "user", "hi from thread two")

    assert history.get_messages("t1") == [{"role": "user", "content": "hi from thread one"}]
    assert history.get_messages("t2") == [{"role": "user", "content": "hi from thread two"}]


def test_history_is_capped_to_max_turns():
    history = ConversationHistory(max_turns=4)
    for i in range(6):
        history.append("t1", "user", f"turn {i}")

    turns = history.get_messages("t1")
    assert len(turns) == 4
    assert [t["content"] for t in turns] == ["turn 2", "turn 3", "turn 4", "turn 5"]


def test_empty_content_is_not_recorded():
    history = ConversationHistory()
    history.append("t1", "assistant", "")
    assert history.get_messages("t1") == []


def test_expired_thread_returns_empty_and_is_dropped():
    history = ConversationHistory(ttl_seconds=0)
    history.append("t1", "user", "hello")
    assert history.get_messages("t1") == []
    # Dropped, not just hidden: a fresh append starts a clean window.
    history.append("t1", "user", "hello again")
    assert history.get_messages("t1") == []  # ttl_seconds=0 -> immediately stale


def test_expire_removes_stale_threads_and_counts_them():
    history = ConversationHistory(ttl_seconds=0)
    history.append("t1", "user", "hello")
    history.append("t2", "user", "hi")

    assert history.expire() == 2
    assert history.expire() == 0


def test_get_messages_returns_a_copy():
    history = ConversationHistory()
    history.append("t1", "user", "hello")
    turns = history.get_messages("t1")
    turns.append({"role": "user", "content": "mutated"})

    assert history.get_messages("t1") == [{"role": "user", "content": "hello"}]
