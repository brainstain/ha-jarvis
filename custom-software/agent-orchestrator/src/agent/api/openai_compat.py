"""OpenAI-compatible /v1 adapter so Open WebUI can talk to the agent.

Wraps the agent's /chat endpoint behind the standard chat completions API.
Streaming is supported (fake — the full response is emitted as one chunk
followed by [DONE], since the underlying graphs are synchronous).

Thread continuity: thread_id is derived from a hash of the opening messages
so the same Open WebUI conversation maps to the same agent session.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import AsyncIterator

import structlog
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.api.routes import chat as handle_chat
from agent.api.schemas import ChatRequest

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/v1")


# ── Request / response models ──────────────────────────────────────────────

class OAIMessage(BaseModel):
    role: str
    content: str | None = None


class OAIChatRequest(BaseModel):
    model: str = "agent"
    messages: list[OAIMessage]
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None
    user: str | None = None


# ── Helpers ────────────────────────────────────────────────────────────────

def _thread_id(messages: list[OAIMessage]) -> str:
    """Stable thread per conversation: hash of the first user message."""
    seed = next((m.content or "" for m in messages if m.role == "user"), "")
    return "webui-" + hashlib.sha256(seed.encode()).hexdigest()[:16]


async def _stream(compl_id: str, reply: str) -> AsyncIterator[str]:
    chunk = {
        "id": compl_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "agent",
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": reply}, "finish_reason": None}],
    }
    yield f"data: {json.dumps(chunk)}\n\n"
    done = {
        "id": compl_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "agent",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(done)}\n\n"
    yield "data: [DONE]\n\n"


# ── Routes ─────────────────────────────────────────────────────────────────

@router.get("/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {"id": "agent", "object": "model", "created": 0, "owned_by": "homelab"}
        ],
    }


@router.post("/chat/completions")
async def chat_completions(request: OAIChatRequest):
    last_user = next(
        (m.content or "" for m in reversed(request.messages) if m.role == "user"),
        "",
    )
    if not last_user:
        return {"error": {"message": "No user message found", "type": "invalid_request_error"}}

    chat_req = ChatRequest(
        message=last_user,
        user_id=request.user or "webui",
        scope="family",
        source="webui",
        thread_id=_thread_id(request.messages),
    )

    result = await handle_chat(chat_req)
    reply = result.message
    compl_id = "chatcmpl-" + uuid.uuid4().hex[:8]

    log.info("openai_compat_chat", tools_used=result.tools_used, channel=result.output_channel)

    if request.stream:
        return StreamingResponse(_stream(compl_id, reply), media_type="text/event-stream")

    return {
        "id": compl_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request.model,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
