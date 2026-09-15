"""HA's conversation-agent bridge — POST /ha/conversation/process.

The endpoint only checks that an Authorization: Bearer header is present;
it doesn't verify the token value against a real Home Assistant instance
(see agent/integrations/ha.py's docstring), so this runs against the
orchestrator alone and does not require HA itself to be reachable.
"""

from __future__ import annotations

import uuid

import httpx

from conftest import require


def test_ha_conversation_process(endpoints, budgets, perf, reachability):
    require(reachability, "orchestrator", "litellm")

    resp, _ = perf.timed(
        "ha/conversation_process",
        lambda: httpx.post(
            f"{endpoints.orchestrator_url}/ha/conversation/process",
            headers={"Authorization": "Bearer e2e-test-token"},
            json={
                "text": "Reply with exactly one word: pong.",
                "language": "en",
                "conversation_id": str(uuid.uuid4()),
            },
            timeout=60.0,
        ),
        budget=budgets.chat_simple,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["response"]["speech"]["plain"]["speech"]
    assert body["response"]["response_type"] == "action_done"


def test_ha_conversation_requires_bearer_token(endpoints, reachability):
    require(reachability, "orchestrator")
    resp = httpx.post(
        f"{endpoints.orchestrator_url}/ha/conversation/process",
        json={"text": "hi"},
        timeout=10.0,
    )
    assert resp.status_code == 401


def test_ha_health(endpoints, budgets, perf, reachability):
    require(reachability, "orchestrator")
    resp, _ = perf.timed(
        "ha/health",
        lambda: httpx.get(f"{endpoints.orchestrator_url}/ha/health", timeout=5.0),
        budget=budgets.health,
    )
    assert resp.status_code == 200
