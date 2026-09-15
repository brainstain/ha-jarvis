"""The OpenAI-compatible /v1 adapter — what Open WebUI actually talks to."""

from __future__ import annotations

import httpx

from conftest import require


def test_list_models(endpoints, budgets, perf, reachability):
    require(reachability, "orchestrator")
    resp, _ = perf.timed(
        "orchestrator/v1_models",
        lambda: httpx.get(f"{endpoints.orchestrator_url}/v1/models", timeout=10.0),
        budget=budgets.health,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert any(m["id"] == "agent" for m in data)


def test_chat_completions_non_streaming(endpoints, budgets, perf, reachability):
    require(reachability, "orchestrator", "litellm")

    resp, _ = perf.timed(
        "orchestrator/v1_chat_completions",
        lambda: httpx.post(
            f"{endpoints.orchestrator_url}/v1/chat/completions",
            json={
                "model": "agent",
                "messages": [{"role": "user", "content": "Reply with exactly one word: pong."}],
                "stream": False,
            },
            timeout=60.0,
        ),
        budget=budgets.openai_compat,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"]


def test_chat_completions_rejects_empty_conversation(endpoints, reachability):
    require(reachability, "orchestrator")
    resp = httpx.post(
        f"{endpoints.orchestrator_url}/v1/chat/completions",
        json={"model": "agent", "messages": [{"role": "system", "content": "no user turn"}]},
        timeout=10.0,
    )
    assert resp.status_code == 200
    assert resp.json()["error"]["type"] == "invalid_request_error"
