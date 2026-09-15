"""Smoke + latency checks for every backing service the orchestrator depends
on. These don't exercise business logic — just "is it up, and how fast."
"""

from __future__ import annotations

import httpx

from conftest import require


def test_litellm_health(endpoints, budgets, perf, reachability):
    """/health/liveliness, not /health — the plain /health endpoint does a
    deep per-deployment check and can hang for a long time against a cold
    model; liveliness is the fast "is the proxy process up" check.
    """
    require(reachability, "litellm")
    resp, _ = perf.timed(
        "infra/litellm_health",
        lambda: httpx.get(f"{endpoints.litellm_url}/health/liveliness", timeout=5.0),
        budget=budgets.health,
    )
    assert resp.status_code == 200


def test_litellm_models_registered(endpoints, budgets, perf, reachability):
    require(reachability, "litellm")
    resp, _ = perf.timed(
        "infra/litellm_models",
        lambda: httpx.get(f"{endpoints.litellm_url}/v1/models", timeout=5.0),
        budget=budgets.health,
    )
    assert resp.status_code == 200
    models = {m["id"] for m in resp.json().get("data", [])}
    for expected in ("assistant", "assistant-fast", "embeddings"):
        assert expected in models, f"{expected!r} missing from litellm_config.yaml model_list"


def test_qdrant_ready(endpoints, budgets, perf, reachability):
    require(reachability, "qdrant")
    resp, _ = perf.timed(
        "infra/qdrant_ready",
        lambda: httpx.get(f"{endpoints.qdrant_url}/readyz", timeout=5.0),
        budget=budgets.health,
    )
    assert resp.status_code == 200


def test_qdrant_collections_reachable(endpoints, budgets, perf, reachability):
    require(reachability, "qdrant")
    resp, _ = perf.timed(
        "infra/qdrant_collections",
        lambda: httpx.get(f"{endpoints.qdrant_url}/collections", timeout=5.0),
        budget=budgets.health,
    )
    assert resp.status_code == 200


def test_orchestrator_health(endpoints, budgets, perf, reachability):
    require(reachability, "orchestrator")
    resp, _ = perf.timed(
        "infra/orchestrator_health",
        lambda: httpx.get(f"{endpoints.orchestrator_url}/health", timeout=10.0),
        budget=budgets.health,
    )
    assert resp.status_code == 200


def test_open_webui_reachable(endpoints, budgets, perf, reachability):
    require(reachability, "open_webui")
    resp, _ = perf.timed(
        "infra/open_webui",
        lambda: httpx.get(endpoints.open_webui_url, timeout=5.0),
        budget=budgets.health,
    )
    assert resp.status_code == 200


def test_ollama_agent_tags(endpoints, budgets, perf, reachability):
    require(reachability, "ollama_agent")
    resp, _ = perf.timed(
        "infra/ollama_agent_tags",
        lambda: httpx.get(f"{endpoints.ollama_agent_url}/api/tags", timeout=5.0),
        budget=budgets.health,
    )
    assert resp.status_code == 200


def test_ollama_inference_tags(endpoints, budgets, perf, reachability):
    require(reachability, "ollama_inference")
    resp, _ = perf.timed(
        "infra/ollama_inference_tags",
        lambda: httpx.get(f"{endpoints.ollama_inference_url}/api/tags", timeout=5.0),
        budget=budgets.health,
    )
    assert resp.status_code == 200
    tags = {m["name"] for m in resp.json().get("models", [])}
    assert any(t.startswith("qwen3") for t in tags), "expected a qwen3 model pulled on the inference node"
