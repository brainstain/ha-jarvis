"""Request/response models for the orchestrator API (per SPEC.md)."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Scope(StrEnum):
    FAMILY = "family"
    PERSONAL = "personal"


class MemoryState(StrEnum):
    """Lifecycle states for a stored memory."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    PATTERN = "pattern"
    REJECTED = "rejected"


class ChatRequest(BaseModel):
    message: str
    user_id: str
    scope: Literal["family", "personal"]
    thread_id: str | None = None
    speaker_id: str | None = None
    source: Literal["voice", "webui", "ha", "notification"] = "webui"
    satellite_id: str | None = None
    metadata: dict | None = None


class ChatResponse(BaseModel):
    message: str
    thread_id: str
    output_channel: str
    task_id: str | None = None
    tools_used: list[str] = Field(default_factory=list)
    memory_updates: list[dict] = Field(default_factory=list)
    confidence: float = 1.0


class RoutingDecision(BaseModel):
    """Output of the meta-reasoning router."""

    intent: Literal["command", "question", "research", "diagnostic", "conversation"]
    graph: Literal["simple", "multistep", "research", "interactive"]
    tools_needed: list[str] = Field(default_factory=list)
    execution_mode: Literal["sync", "async"] = "sync"
    output_channel: Literal["voice", "webui", "push"] = "webui"
    parallel_steps: bool = False


class TaskStatus(BaseModel):
    task_id: str
    thread_id: str
    status: Literal["queued", "running", "awaiting_input", "complete", "failed", "cancelled"]
    result: str | None = None
    partial: list[str] = Field(default_factory=list)
    question: str | None = None  # populated when awaiting_input (HITL)
    created_at: datetime
    updated_at: datetime


class ThreadInfo(BaseModel):
    thread_id: str
    user_id: str
    question: str | None = None
    created_at: datetime


class ResumeRequest(BaseModel):
    """User input resuming a HITL-interrupted workflow."""

    response: str
    user_id: str


class Memory(BaseModel):
    id: str | None = None
    text: str
    user_id: str
    scope: Literal["family", "personal"]
    memory_type: str = "fact"
    state: MemoryState = MemoryState.PENDING
    tags: list[str] = Field(default_factory=list)
    source: str = "conversation"
    timestamp: datetime | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    checks: dict[str, str]
