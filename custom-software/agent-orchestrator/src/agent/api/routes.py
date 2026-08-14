"""HTTP endpoints for the Agent Orchestrator."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import structlog
from fastapi import APIRouter, HTTPException

from agent import __version__
from agent.api.schemas import (
    ChatRequest,
    ChatResponse,
    HealthResponse,
    ResumeRequest,
    TaskStatus,
    ThreadInfo,
)
from agent.config import get_settings
from agent.core.output import OutputRouter
from agent.core.router import MetaRouter
from agent.core.safety import SafetyGuard
from agent.core.session import SessionManager

log = structlog.get_logger(__name__)
router = APIRouter()

settings = get_settings()
sessions = SessionManager(session_timeout_seconds=settings.session_timeout_seconds)
meta_router = MetaRouter(settings)
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

    status = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
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

    # Graph execution is wired up as the MCP hub and node implementations land;
    # the routing decision above is already live and drives the response shape.
    raise HTTPException(
        status_code=501,
        detail=(
            f"Graph '{decision.graph}' not yet wired. Routing works: "
            f"intent={decision.intent}, tools={decision.tools_needed}, channel={channel}"
        ),
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
