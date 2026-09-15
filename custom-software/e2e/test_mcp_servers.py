"""Direct MCP stdio round-trips — the same MCPClientHub the orchestrator
uses to spawn mcp-memory-scoped, mcp-notifications and mcp-workflow-status
(agent-orchestrator/src/agent/mcp/client.py), reused here so this suite
exercises the real client code path rather than a reimplementation.

Requires the three server packages installed in this environment, since
the "stdio" transport is literally `<this interpreter> -m <package>`:
    pip install -e ../mcp-memory-scoped -e ../mcp-notifications -e ../mcp-workflow-status

Every tool call here is read-only against the backing store, on purpose:
this suite may run against a live homelab deployment, and a test has no
business writing into someone's actual memory or workflow state.
"""

from __future__ import annotations

import sys

import pytest

from conftest import require

pytest.importorskip("mcp_memory_scoped", reason="pip install -e ../mcp-memory-scoped")
pytest.importorskip("mcp_notifications", reason="pip install -e ../mcp-notifications")
pytest.importorskip("mcp_workflow_status", reason="pip install -e ../mcp-workflow-status")

from agent.mcp.client import MCPClientHub, MCPServerConfig  # noqa: E402

USER = "e2e-test-user"


def _config(name: str, module: str, env: dict[str, str], timeout: float) -> MCPServerConfig:
    return MCPServerConfig(
        name=name,
        transport="stdio",
        command=sys.executable,
        args=["-m", module],
        env=env,
        timeout=timeout,
    )


@pytest.fixture
async def hub(endpoints):
    configs = [
        _config(
            "mcp-memory-scoped",
            "mcp_memory_scoped",
            {"QDRANT_URL": endpoints.qdrant_url, "LITELLM_URL": endpoints.litellm_url},
            timeout=30,
        ),
        _config(
            "mcp-notifications",
            "mcp_notifications",
            {"HA_URL": endpoints.ha_url, "HA_TOKEN": endpoints.ha_token},
            timeout=15,
        ),
        _config(
            "mcp-workflow-status",
            "mcp_workflow_status",
            {"ORCHESTRATOR_URL": endpoints.orchestrator_url},
            timeout=10,
        ),
    ]
    client_hub = MCPClientHub(configs)
    try:
        yield client_hub
    finally:
        await client_hub.close()


async def test_memory_search_tool_call(hub, budgets, perf, reachability):
    require(reachability, "qdrant", "litellm")

    async def call():
        return await hub.call_tool(
            "mcp-memory-scoped",
            "memory_search",
            {"query": "e2e smoke test", "user_id": USER, "scope": "personal", "limit": 1},
        )

    envelope, _ = await perf.atimed("mcp/memory_search", call, budget=budgets.mcp_tool_call)
    assert "error" not in envelope, envelope.get("error")
    assert isinstance(envelope["result"], list)


async def test_memory_list_tools(hub, budgets, perf, reachability):
    """list_tools() is what the orchestrator calls at startup and for /health."""
    require(reachability, "qdrant", "litellm")

    async def call():
        return await hub.list_tools("mcp-memory-scoped")

    tools, _ = await perf.atimed("mcp/list_tools_memory_scoped", call, budget=budgets.mcp_tool_call)
    assert {"memory_search", "memory_store"} <= {t["name"] for t in tools}


async def test_notifications_list_targets(hub, budgets, perf, reachability):
    require(reachability, "home_assistant")

    async def call():
        return await hub.call_tool("mcp-notifications", "list_notify_targets", {})

    envelope, _ = await perf.atimed("mcp/list_notify_targets", call, budget=budgets.mcp_tool_call)
    assert "error" not in envelope, envelope.get("error")


async def test_workflow_list_pending(hub, budgets, perf, reachability):
    require(reachability, "orchestrator")

    async def call():
        return await hub.call_tool("mcp-workflow-status", "workflow_list_pending", {"user_id": USER})

    envelope, _ = await perf.atimed("mcp/workflow_list_pending", call, budget=budgets.mcp_tool_call)
    assert "error" not in envelope, envelope.get("error")
