"""Real /chat round-trips through the live LLM + LangGraph pipeline.

Not mocked: these exercise the same path a voice command or webui message
takes — MetaRouter classifies intent, the matching graph runs, LiteLLM
answers. Test prompts are plain conversation, not tool commands: which MCP
tools are actually wired up (ha-mcp especially) depends on the environment
the suite runs against, so a tool-free prompt is the one thing guaranteed
to prove the whole chain end to end everywhere.
"""

from __future__ import annotations

import uuid

import httpx

from conftest import require

USER = "e2e-test-user"
PROMPT = "Reply with exactly one word: pong."


def _chat(endpoints, **overrides) -> httpx.Response:
    body = {
        "message": PROMPT,
        "user_id": USER,
        "scope": "personal",
        "source": "webui",
    }
    body.update(overrides)
    return httpx.post(f"{endpoints.orchestrator_url}/chat", json=body, timeout=120.0)


def test_health_contract(endpoints, budgets, perf, reachability):
    require(reachability, "orchestrator")
    resp, _ = perf.timed(
        "orchestrator/health",
        lambda: httpx.get(f"{endpoints.orchestrator_url}/health", timeout=10.0),
        budget=budgets.health,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("ok", "degraded")
    assert "litellm" in body["checks"]
    assert "version" in body


def test_simple_chat_round_trip(endpoints, budgets, perf, reachability):
    require(reachability, "orchestrator", "litellm")
    thread_id = str(uuid.uuid4())

    resp, _ = perf.timed(
        "orchestrator/chat_simple",
        lambda: _chat(endpoints, thread_id=thread_id),
        budget=budgets.chat_simple,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["thread_id"] == thread_id
    assert body["message"]
    assert 0.0 <= body["confidence"] <= 1.0


def test_chat_latency_sample(endpoints, budgets, perf, reachability):
    """A handful of sequential reps for a meaningful p50/p95 spread — this is
    a latency sample, not a load test: one request in flight at a time.
    """
    require(reachability, "orchestrator", "litellm")
    for _ in range(3):
        resp, _ = perf.timed(
            "orchestrator/chat_simple",
            lambda: _chat(endpoints, thread_id=str(uuid.uuid4())),
            budget=budgets.chat_simple,
        )
        assert resp.status_code == 200


def test_voice_source_gets_voice_output_channel(endpoints, budgets, perf, reachability):
    require(reachability, "orchestrator", "litellm")
    resp, _ = perf.timed(
        "orchestrator/chat_voice",
        lambda: _chat(
            endpoints,
            thread_id=str(uuid.uuid4()),
            source="voice",
            satellite_id="e2e-test-satellite",
        ),
        budget=budgets.chat_simple,
    )
    assert resp.status_code == 200
    assert resp.json()["output_channel"] == "voice"


def test_async_chat_returns_a_pollable_task(endpoints, budgets, perf, reachability):
    """/chat/async is the always-available async entry point.

    NOTE: as of this writing, `_queue_task` (agent/api/routes.py) registers
    the task but nothing dispatches the Celery `run_research` task against
    it, so a queued task never progresses past "queued" in this deployment.
    We assert the contract that's actually implemented — enqueue + status
    lookup — rather than polling for a completion that can't happen yet.
    """
    require(reachability, "orchestrator")
    thread_id = str(uuid.uuid4())

    resp, _ = perf.timed(
        "orchestrator/chat_async_enqueue",
        lambda: httpx.post(
            f"{endpoints.orchestrator_url}/chat/async",
            json={
                "message": "Look into something for me.",
                "user_id": USER,
                "scope": "personal",
                "source": "webui",
                "thread_id": thread_id,
            },
            timeout=15.0,
        ),
        budget=budgets.health,
    )
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]
    assert task_id

    status_resp, _ = perf.timed(
        "orchestrator/task_status",
        lambda: httpx.get(f"{endpoints.orchestrator_url}/tasks/{task_id}", timeout=10.0),
        budget=budgets.health,
    )
    assert status_resp.status_code == 200
    task = status_resp.json()
    assert task["task_id"] == task_id
    assert task["status"] in (
        "queued", "running", "awaiting_input", "complete", "failed", "cancelled",
    )


def test_unknown_task_id_is_404(endpoints, reachability):
    require(reachability, "orchestrator")
    resp = httpx.get(f"{endpoints.orchestrator_url}/tasks/does-not-exist", timeout=10.0)
    assert resp.status_code == 404
