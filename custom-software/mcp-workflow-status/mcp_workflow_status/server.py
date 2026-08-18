"""Async workflow and HITL interrupt management MCP server.

Gives the LLM visibility into running and paused tasks so it can:
- Check if a prior research request is done
- List what's waiting for user input
- Resume or cancel a paused workflow

Talks to the agent-orchestrator REST API rather than directly to the DB,
so there's one source of truth for task state.

Environment variables:
  ORCHESTRATOR_URL  http://agent-orchestrator:8100
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog
from fastmcp import FastMCP

log = structlog.get_logger(__name__)

ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://agent-orchestrator:8100")

mcp = FastMCP("mcp-workflow-status")


async def _get(path: str) -> Any:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{ORCHESTRATOR_URL}{path}")
        resp.raise_for_status()
        return resp.json()


async def _post(path: str, body: dict[str, Any]) -> Any:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{ORCHESTRATOR_URL}{path}", json=body)
        resp.raise_for_status()
        return resp.json()


async def _delete(path: str) -> Any:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.delete(f"{ORCHESTRATOR_URL}{path}")
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
async def workflow_list_pending(user_id: str) -> list[dict[str, Any]]:
    """List workflows that are paused and waiting for this user's input."""
    try:
        return await _get(f"/tasks/pending?user_id={user_id}")
    except httpx.HTTPError as exc:
        return [{"error": str(exc)}]


@mcp.tool()
async def workflow_get_status(task_id: str) -> dict[str, Any]:
    """Get the current status and result of an async task."""
    try:
        return await _get(f"/tasks/{task_id}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return {"error": "task not found", "task_id": task_id}
        return {"error": str(exc)}


@mcp.tool()
async def workflow_resume(task_id: str, user_id: str, response: str) -> dict[str, Any]:
    """Resume a HITL-paused workflow with the user's answer.

    response: the user's answer to the question the agent asked.
    """
    try:
        return await _post(f"/tasks/{task_id}/resume", {"user_id": user_id, "response": response})
    except httpx.HTTPError as exc:
        return {"error": str(exc), "resumed": False}


@mcp.tool()
async def workflow_cancel(task_id: str) -> dict[str, Any]:
    """Cancel a running or queued workflow."""
    try:
        return await _delete(f"/tasks/{task_id}")
    except httpx.HTTPError as exc:
        return {"error": str(exc), "cancelled": False}


@mcp.tool()
async def workflow_list_recent(user_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """List the most recent completed and failed tasks for this user.

    Note: this calls the orchestrator which currently keeps tasks in memory.
    Full persistence arrives with Celery result backend in Phase 3.
    """
    try:
        pending = await _get(f"/tasks/pending?user_id={user_id}")
        return pending[:limit]
    except httpx.HTTPError as exc:
        return [{"error": str(exc)}]
