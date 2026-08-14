"""HTTP endpoints for the Agent Orchestrator."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, HTTPException

from agent import __version__
from agent.api.schemas import (
    ChatRequest,
    ChatResponse,
    HealthResponse,
    ResumeRequest,
    RoutingDecision,
    TaskStatus,
    ThreadInfo,
)
from agent.config import get_settings
from agent.core.llm import LLMClient
from agent.core.output import OutputRouter
from agent.core.router import MetaRouter
from agent.core.safety import SafetyGuard
from agent.core.session import SessionManager
from agent.graphs.simple import build_simple_graph
from agent.mcp.registry import MCPToolRegistry, render_openai_tools
from agent.mcp.tool_filter import ToolFilter

log = structlog.get_logger(__name__)
router = APIRouter()

settings = get_settings()
sessions = SessionManager(session_timeout_seconds=settings.session_timeout_seconds)
meta_router = MetaRouter(settings)
llm = LLMClient(settings)
guard = SafetyGuard(
    max_iterations=settings.max_iterations,
    token_budget=settings.token_budget,
    circuit_breaker_threshold=settings.circuit_breaker_threshold,
    circuit_breaker_cooldown=settings.circuit_breaker_cooldown,
    loop_window=settings.loop_detection_window,
    loop_repeats=settings.loop_detection_repeats,
)

# In-memory task store. Celery-backed persistence lands with tasks/research.py;
# until then async requests are tracked here so the API contract is stable.
_tasks: dict[str, TaskStatus] = {}

# Populated by the app lifespan once MCP discovery has run.
_mcp: MCPToolRegistry | None = None
_tool_filter: ToolFilter | None = None

MCP_HEALTH_TIMEOUT = 5.0

SYNTHESIS_SYSTEM = (
    "You are a home assistant. Answer the user from the tool result you are given. "
    "Be direct and specific; never describe the tool or the mechanics of the call."
)
VOICE_HINT = " Your answer is spoken aloud: one or two short sentences, no lists or markup."


def set_tools(mcp: MCPToolRegistry | None) -> None:
    """Install (or clear) the discovered MCP tool registry.

    Called from the app lifespan after discovery, so this module keeps no
    import-time dependency on live MCP servers — routing and the test suite
    work fine with none connected.
    """
    global _mcp, _tool_filter
    _mcp = mcp
    _tool_filter = ToolFilter(mcp.registry) if mcp is not None else None
    if mcp is not None:
        log.info("tools_installed", tools=len(mcp.tool_names), servers=sorted(mcp.hub.servers))


def get_tools() -> MCPToolRegistry | None:
    """The active MCP registry, or None before discovery / when unconfigured."""
    return _mcp


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness plus a quick dependency probe."""
    checks: dict[str, str] = {}
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{settings.litellm_base_url}/models")
            checks["litellm"] = "ok" if response.status_code < 500 else "degraded"
    except httpx.HTTPError:
        checks["litellm"] = "unreachable"

    if _mcp is None:
        checks["mcp"] = "unconfigured"
    else:
        try:
            statuses = await asyncio.wait_for(_mcp.health(), timeout=MCP_HEALTH_TIMEOUT)
        except TimeoutError:
            checks["mcp"] = "timeout"
        else:
            checks["mcp"] = "ok" if all(v == "ok" for v in statuses.values()) else "degraded"
            checks["mcp_tools"] = str(len(_mcp.tool_names))
            for server, state in statuses.items():
                checks[f"mcp:{server}"] = state

    # mcp_tools is a count, not a verdict — don't let it decide overall status.
    verdicts = {k: v for k, v in checks.items() if k != "mcp_tools"}
    status = "ok" if all(v == "ok" for v in verdicts.values()) else "degraded"
    return HealthResponse(status=status, version=__version__, checks=checks)


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Synchronous chat — used by the voice pipeline and HA conversation agent."""
    thread_id = sessions.resolve_thread(request)
    decision = await meta_router.route(
        request.message,
        {
            "scope": request.scope,
            "source": request.source,
            "user_id": request.user_id,
            "time_of_day": datetime.now(UTC).strftime("%H:%M"),
        },
    )
    channel = OutputRouter.resolve(request, decision)

    log.info(
        "chat_routed",
        thread_id=thread_id,
        intent=decision.intent,
        graph=decision.graph,
        mode=decision.execution_mode,
        channel=channel,
    )

    if decision.execution_mode == "async":
        task_id = _queue_task(thread_id, request)
        return ChatResponse(
            message="I'll look into that and let you know when it's ready.",
            thread_id=thread_id,
            output_channel=channel,
            task_id=task_id,
            confidence=1.0,
        )

    if decision.graph == "simple":
        return await _run_simple(request, thread_id, decision, channel)

    # multistep / research / interactive land with their node implementations.
    raise HTTPException(
        status_code=501,
        detail=(
            f"Graph '{decision.graph}' not yet wired. Routing works: "
            f"intent={decision.intent}, tools={decision.tools_needed}, channel={channel}"
        ),
    )


# ──────────────────────────────────────────────────────────────────────
# Simple-command execution
# ──────────────────────────────────────────────────────────────────────


def _select_tools(decision: RoutingDecision) -> list[Any]:
    """Tools visible for this request: category filter, minus tripped breakers."""
    if _tool_filter is None or not decision.tools_needed:
        return []
    selected = _tool_filter.select_tools(
        decision.tools_needed, max_tools=settings.max_tools_per_request
    )
    usable = [t for t in selected if not guard.check_circuit_breaker(t.name)]
    if len(usable) != len(selected):
        skipped = [t.name for t in selected if t not in usable]
        log.info("tools_skipped_circuit_open", tools=skipped)
    return usable


async def _run_simple(
    request: ChatRequest, thread_id: str, decision: RoutingDecision, channel: str
) -> ChatResponse:
    """Execute the simple-command graph with real MCP tool calls."""
    speech = OutputRouter.is_speech(channel)
    system = SYNTHESIS_SYSTEM + (VOICE_HINT if speech else "")

    async def memory_lookup(state: dict[str, Any]) -> list[dict[str, Any]]:
        # Recall arrives with mcp-memory-scoped; until that server exists the
        # graph runs without it rather than blocking tool execution.
        return []

    async def execute_tool(state: dict[str, Any]) -> dict[str, Any]:
        tools = _select_tools(decision)
        if not tools or _mcp is None:
            log.info("no_tools_available", categories=decision.tools_needed)
            return {}

        try:
            message = await llm.complete(
                [
                    {
                        "role": "system",
                        "content": (
                            "Pick the single tool that answers the user's request, or "
                            "answer directly if no tool fits."
                        ),
                    },
                    {"role": "user", "content": request.message},
                ],
                tools=render_openai_tools(tools),
            )
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.warning("tool_selection_failed", error=str(exc))
            return {}

        calls = message.get("tool_calls") or []
        if not calls:
            return {}

        call = calls[0]
        name = call.get("function", {}).get("name", "")
        try:
            args = json.loads(call.get("function", {}).get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        if not isinstance(args, dict):
            args = {}

        # Re-check: the breaker may have tripped between selection and now.
        if guard.check_circuit_breaker(name):
            return {
                "tool_calls": [{"tool": name, "args": args, "error": "circuit_open"}],
                "tools_used": [name],
            }

        envelope = await _mcp.call(name, args)
        return {"tool_calls": [{**envelope, "args": args}], "tools_used": [name]}

    async def synthesize(state: dict[str, Any]) -> dict[str, Any]:
        calls = state.get("tool_calls", [])
        last = calls[-1] if calls else None

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": request.message},
        ]
        if last is not None:
            outcome = (
                f"error: {last['error']}"
                if last.get("error")
                else json.dumps(last.get("result"), default=str)
            )
            messages.append(
                {"role": "assistant", "content": f"Tool {last.get('tool')} returned {outcome}"}
            )

        confidence = 0.7
        if last is not None:
            confidence = 0.4 if last.get("error") else 0.9

        try:
            reply = await llm.complete(messages)
            text = (reply.get("content") or "").strip()
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.warning("synthesis_failed", error=str(exc))
            text = ""

        if not text:
            text = "I couldn't complete that one." if last is None or last.get("error") else "Done."
            confidence = min(confidence, 0.3)

        return {"response": text, "confidence": confidence}

    graph = build_simple_graph(memory_lookup, execute_tool, synthesize, guard)
    final = await graph.ainvoke(
        {
            "message": request.message,
            "user_id": request.user_id,
            "scope": request.scope,
            "thread_id": thread_id,
            "source": request.source,
            "output_channel": channel,
        }
    )

    return ChatResponse(
        message=final.get("response", ""),
        thread_id=thread_id,
        output_channel=channel,
        tools_used=final.get("tools_used", []),
        memory_updates=final.get("memory_updates", []),
        confidence=final.get("confidence", 1.0),
    )


@router.post("/chat/async", response_model=ChatResponse)
async def chat_async(request: ChatRequest) -> ChatResponse:
    """Queue an async task and return its id immediately."""
    thread_id = sessions.resolve_thread(request)
    task_id = _queue_task(thread_id, request)
    return ChatResponse(
        message="Queued.",
        thread_id=thread_id,
        output_channel="push",
        task_id=task_id,
    )


@router.get("/tasks/pending", response_model=list[ThreadInfo])
async def pending_tasks(user_id: str) -> list[ThreadInfo]:
    """Workflows awaiting this user's input (HITL interrupts)."""
    return sessions.get_pending_threads(user_id)


@router.get("/tasks/{task_id}", response_model=TaskStatus)
async def task_status(task_id: str) -> TaskStatus:
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Unknown task_id")
    return task


@router.post("/tasks/{task_id}/resume", response_model=TaskStatus)
async def resume_task(task_id: str, request: ResumeRequest) -> TaskStatus:
    """Resume a HITL-interrupted workflow with the user's answer."""
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Unknown task_id")
    if task.status != "awaiting_input":
        raise HTTPException(status_code=409, detail=f"Task is {task.status}, not awaiting input")

    sessions.clear_pending(task.thread_id)
    task.status = "running"
    task.question = None
    task.updated_at = datetime.now(UTC)
    return task


@router.delete("/tasks/{task_id}", response_model=TaskStatus)
async def cancel_task(task_id: str) -> TaskStatus:
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Unknown task_id")
    if task.status in {"complete", "failed", "cancelled"}:
        return task
    task.status = "cancelled"
    task.updated_at = datetime.now(UTC)
    sessions.clear_pending(task.thread_id)
    return task


def _queue_task(thread_id: str, request: ChatRequest) -> str:
    task_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    _tasks[task_id] = TaskStatus(
        task_id=task_id,
        thread_id=thread_id,
        status="queued",
        created_at=now,
        updated_at=now,
    )
    log.info("task_queued", task_id=task_id, thread_id=thread_id, user_id=request.user_id)
    return task_id
