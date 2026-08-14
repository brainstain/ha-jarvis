"""Safety stack: iteration limits, token budget, loop detection, circuit breakers."""

import time

from agent.core.safety import SafetyGuard, TokenBudgetManager


def test_iteration_limit():
    guard = SafetyGuard(max_iterations=3)
    assert guard.check_iteration_limit({"iteration_count": 2}) is True
    assert guard.check_iteration_limit({"iteration_count": 3}) is False


def test_token_budget_verdicts():
    guard = SafetyGuard(token_budget=1000)
    assert guard.check_token_budget({"tokens_used": 100}) == "ok"
    assert guard.check_token_budget({"tokens_used": 850}) == "compress"
    assert guard.check_token_budget({"tokens_used": 1000}) == "stop"


def test_loop_detection_triggers_on_repeat():
    guard = SafetyGuard(loop_window=5, loop_repeats=3)
    call = {"tool": "ha.light_toggle", "args": {"entity": "light.kitchen"}}
    assert guard.detect_loop({"tool_calls": [call, call]}) is False
    assert guard.detect_loop({"tool_calls": [call, call, call]}) is True


def test_loop_detection_ignores_varied_calls():
    guard = SafetyGuard()
    calls = [
        {"tool": "ha.light_toggle", "args": {"entity": f"light.{room}"}}
        for room in ("kitchen", "office", "porch")
    ]
    assert guard.detect_loop({"tool_calls": calls}) is False


def test_circuit_breaker_trips_after_threshold():
    guard = SafetyGuard(circuit_breaker_threshold=3, circuit_breaker_cooldown=60)
    for _ in range(2):
        guard.record_failure("mcp-calendar.list")
    assert guard.check_circuit_breaker("mcp-calendar.list") is False

    guard.record_failure("mcp-calendar.list")
    assert guard.check_circuit_breaker("mcp-calendar.list") is True


def test_circuit_breaker_resets_on_success():
    guard = SafetyGuard(circuit_breaker_threshold=2)
    guard.record_failure("t")
    guard.record_failure("t")
    assert guard.check_circuit_breaker("t") is True
    guard.record_success("t")
    assert guard.check_circuit_breaker("t") is False


def test_circuit_breaker_half_opens_after_cooldown():
    guard = SafetyGuard(circuit_breaker_threshold=1, circuit_breaker_cooldown=0)
    guard.record_failure("t")
    time.sleep(0.01)
    assert guard.check_circuit_breaker("t") is False


def test_token_counting_is_nonzero_for_text():
    assert TokenBudgetManager.count_tokens("") == 0
    assert TokenBudgetManager.count_tokens("hello world") > 0


def test_compression_masks_old_tool_output_but_keeps_recent():
    budget = TokenBudgetManager(budget=1000)
    state = {
        "messages": [
            {"role": "system", "content": "You are Jarvis."},
            {"role": "tool", "content": "x" * 500},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "and then?"},
            {"role": "assistant", "content": "sure"},
        ],
        "tokens_used": 900,
    }
    compressed = budget.compress_context(state)

    assert compressed["compressed"] is True
    assert compressed["messages"][0]["role"] == "system"
    assert compressed["messages"][1]["content"].startswith("[Result:")
    # Last three messages preserved verbatim.
    assert compressed["messages"][-3:] == state["messages"][-3:]
    assert compressed["tokens_used"] < state["tokens_used"]


def test_compression_noop_on_short_chains():
    budget = TokenBudgetManager()
    state = {"messages": [{"role": "user", "content": "hi"}], "tokens_used": 10}
    assert budget.compress_context(state) == state
