"""Celery application and task definitions.

The Celery worker is the same Docker image as the FastAPI process, launched
with a different CMD:
  celery -A agent.tasks worker --loglevel=info --concurrency=2

It shares agent.config, so it connects to the same Qdrant, LiteLLM, Redis,
and MCP servers as the API process — just in a separate event loop.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog
from celery import Celery

from agent.config import get_settings

log = structlog.get_logger(__name__)
settings = get_settings()

app = Celery(
    "agent",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,    # one in-flight task per worker thread
    result_expires=86_400,           # purge results after 24 h
)


# ──────────────────────────────────────────────────────────────────────
# Research task
# ──────────────────────────────────────────────────────────────────────


@app.task(bind=True, name="agent.tasks.run_research", max_retries=1, default_retry_delay=10)
def run_research(
    self,
    message: str,
    user_id: str,
    scope: str,
    thread_id: str,
    task_id: str,
    tools_needed: list[str],
) -> dict[str, Any]:
    """Parallel search + synthesis for long-running research requests.

    Runs inside ``asyncio.run()`` because each Celery task gets its own thread
    (and therefore its own event loop).  Results are delivered via ntfy push
    when complete.
    """
    try:
        result = asyncio.run(
            _research_async(message, user_id, scope, thread_id, tools_needed)
        )
        _push_result(task_id, user_id, result["response"])
        return result
    except Exception as exc:  # noqa: BLE001
        log.error("research_task_failed", task_id=task_id, error=str(exc))
        _push_result(task_id, user_id, "I ran into a problem with your research request.")
        raise self.retry(exc=exc)


async def _research_async(
    message: str,
    user_id: str,
    scope: str,
    thread_id: str,
    tools_needed: list[str],
) -> dict[str, Any]:
    """Async core of the research task.

    Phase 1: ask the LLM to select tools and fire them in parallel.
    Phase 2: synthesize all results into a cohesive answer.
    """
    from agent.core.llm import LLMClient
    from agent.mcp.client import MCPClientHub, load_server_configs
    from agent.mcp.registry import MCPToolRegistry, render_openai_tools
    from agent.mcp.tool_filter import ToolFilter, ToolRegistry

    llm = LLMClient(settings)

    # Fresh MCP connections per task (different event loop from the API process)
    try:
        configs = load_server_configs(settings.mcp_config_path)
    except Exception as exc:  # noqa: BLE001
        log.warning("research_mcp_config_failed", error=str(exc))
        configs = []

    hub = MCPClientHub(configs)
    mcp = MCPToolRegistry(hub, configs)
    tool_registry = await mcp.discover()
    tool_filter = ToolFilter(tool_registry)
    selected = tool_filter.select_tools(tools_needed, max_tools=7)

    # ── Phase 1: parallel search ──────────────────────────────
    plan_messages = [
        {
            "role": "system",
            "content": (
                "You are a research assistant. To answer the user's question comprehensively, "
                "call multiple tools in parallel. Prefer search and document tools."
            ),
        },
        {"role": "user", "content": message},
    ]
    try:
        plan_reply = await llm.complete(plan_messages, tools=render_openai_tools(selected))
    except Exception as exc:  # noqa: BLE001
        log.warning("research_planning_failed", error=str(exc))
        await hub.close()
        return {"response": "Unable to plan this research right now.", "confidence": 0.2}

    calls = plan_reply.get("tool_calls") or []
    results: list[dict[str, Any]] = []
    if calls:
        coros = []
        for call in calls[:5]:   # cap parallel fan-out
            fn = call.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            coros.append(mcp.call(name, args))

        gathered = await asyncio.gather(*coros, return_exceptions=True)
        results = [r for r in gathered if isinstance(r, dict)]

    # ── Phase 2: synthesize ───────────────────────────────────
    synth_messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "You are a research assistant. Synthesize the search results below into "
                "a clear, thorough answer. Cite sources where available."
            ),
        },
        {"role": "user", "content": message},
    ]
    for r in results:
        if r.get("result"):
            synth_messages.append({
                "role": "assistant",
                "content": f"Search result from {r.get('tool')}: "
                           f"{json.dumps(r['result'], default=str)}",
            })

    try:
        final = await llm.complete(synth_messages)
        text = (final.get("content") or "").strip()
    except Exception as exc:  # noqa: BLE001
        log.warning("research_synthesis_failed", error=str(exc))
        text = ""

    await hub.close()
    return {
        "response": text or "I couldn't find a clear answer for that.",
        "confidence": 0.85 if text else 0.2,
        "tools_used": [c.get("function", {}).get("name", "") for c in calls],
    }


def _push_result(task_id: str, user_id: str, message: str) -> None:
    """Best-effort push notification via ntfy when a research task completes."""
    ntfy_url = getattr(settings, "ntfy_url", None)
    if not ntfy_url:
        return
    try:
        import httpx
        httpx.post(
            ntfy_url,
            content=message,
            headers={
                "Title": "Research complete",
                "Tags": "white_check_mark",
                "X-Task-Id": task_id,
                "X-User-Id": user_id,
            },
            timeout=5.0,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("push_notify_failed", task_id=task_id, error=str(exc))
