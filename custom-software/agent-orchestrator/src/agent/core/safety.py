"""Safety stack — iteration limits, token budget, loop detection, circuit breakers."""

from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Literal

BudgetVerdict = Literal["ok", "compress", "stop"]

COMPRESS_THRESHOLD = 0.8


def _fingerprint(tool_name: str, args: dict[str, Any]) -> str:
    """Stable fingerprint of a tool call for loop detection."""
    try:
        rendered = json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        rendered = str(sorted(args.items()))
    return f"{tool_name}:{rendered}"


class TokenBudgetManager:
    """Tracks token usage across a multi-step chain."""

    def __init__(self, budget: int = 50_000) -> None:
        self.budget = budget

    @staticmethod
    def count_tokens(text: str) -> int:
        """Approximate token count (chars/4) — fast, no tokenizer dependency."""
        return max(1, len(text) // 4) if text else 0

    def usage_ratio(self, state: dict[str, Any]) -> float:
        used = int(state.get("tokens_used", 0))
        return used / self.budget if self.budget else 0.0

    def should_compress(self, state: dict[str, Any]) -> bool:
        """True once accumulated tokens exceed 80% of budget."""
        return self.usage_ratio(state) >= COMPRESS_THRESHOLD

    def verdict(self, state: dict[str, Any]) -> BudgetVerdict:
        ratio = self.usage_ratio(state)
        if ratio >= 1.0:
            return "stop"
        if ratio >= COMPRESS_THRESHOLD:
            return "compress"
        return "ok"

    def compress_context(self, state: dict[str, Any]) -> dict[str, Any]:
        """Observation masking: replace old tool outputs with short summaries.

        Preserves the system prompt, the last 3 messages, and the current plan
        step. LLM-based summarization of the remainder is a later escalation
        (see SPEC.md); this implements step 1, which is cheap and lossless
        enough for most chains.
        """
        messages: list[dict[str, Any]] = list(state.get("messages", []))
        if len(messages) <= 4:
            return state

        head, tail = messages[:-3], messages[-3:]
        compressed: list[dict[str, Any]] = []
        for message in head:
            if message.get("role") == "system":
                compressed.append(message)
                continue
            if message.get("role") == "tool":
                content = str(message.get("content", ""))
                summary = content[:120] + ("…" if len(content) > 120 else "")
                compressed.append({**message, "content": f"[Result: {summary}]"})
                continue
            compressed.append(message)

        new_state = dict(state)
        new_state["messages"] = compressed + tail
        new_state["tokens_used"] = sum(
            self.count_tokens(str(m.get("content", ""))) for m in new_state["messages"]
        )
        new_state["compressed"] = True
        return new_state


@dataclass
class SafetyGuard:
    """Enforces all safety limits on agent execution."""

    max_iterations: int = 15
    token_budget: int = 50_000
    circuit_breaker_threshold: int = 3
    circuit_breaker_cooldown: int = 60
    loop_window: int = 5
    loop_repeats: int = 3

    _failures: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _tripped_at: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.budget = TokenBudgetManager(self.token_budget)

    # ── iteration + budget ───────────────────────────────────
    def check_iteration_limit(self, state: dict[str, Any]) -> bool:
        """False once the chain has hit max_iterations."""
        return int(state.get("iteration_count", 0)) < self.max_iterations

    def check_token_budget(self, state: dict[str, Any]) -> BudgetVerdict:
        return self.budget.verdict(state)

    # ── loop detection ───────────────────────────────────────
    def detect_loop(self, state: dict[str, Any]) -> bool:
        """True if the same tool+args repeats within the sliding window."""
        calls = state.get("tool_calls", [])
        if len(calls) < self.loop_repeats:
            return False
        window = deque(calls[-self.loop_window :])
        counts: dict[str, int] = defaultdict(int)
        for call in window:
            counts[_fingerprint(call.get("tool", ""), call.get("args", {}))] += 1
        return any(count >= self.loop_repeats for count in counts.values())

    # ── circuit breakers ─────────────────────────────────────
    def record_failure(self, tool_name: str) -> None:
        self._failures[tool_name] += 1
        if self._failures[tool_name] >= self.circuit_breaker_threshold:
            self._tripped_at[tool_name] = time.monotonic()

    def record_success(self, tool_name: str) -> None:
        self._failures[tool_name] = 0
        self._tripped_at.pop(tool_name, None)

    def check_circuit_breaker(self, tool_name: str) -> bool:
        """True if the tool is currently tripped (should not be called)."""
        tripped = self._tripped_at.get(tool_name)
        if tripped is None:
            return False
        if (time.monotonic() - tripped) >= self.circuit_breaker_cooldown:
            # Cooldown elapsed — half-open: allow one retry.
            self._tripped_at.pop(tool_name, None)
            self._failures[tool_name] = 0
            return False
        return True
