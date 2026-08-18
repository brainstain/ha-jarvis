"""WebSocket streaming endpoint for Open WebUI.

Open WebUI connects to /ws/chat and sends JSON messages. We stream tokens
back from LiteLLM as they arrive so the user sees a live typing effect.

Message format (client → server):
  {
    "message": "turn off the kitchen lights",
    "user_id": "michael",
    "scope": "personal",
    "thread_id": "optional-uuid",
    "history": [{"role": "user"|"assistant", "content": "..."}]
  }

Chunk format (server → client):
  {"type": "chunk",   "content": "token text", "thread_id": "..."}
  {"type": "done",    "thread_id": "..."}
  {"type": "error",   "error": "message"}
"""

from __future__ import annotations

import json
import uuid
from typing import AsyncIterator

import httpx
import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from agent.config import get_settings

log = structlog.get_logger(__name__)
ws_router = APIRouter()
settings = get_settings()

_SYSTEM_PROMPT = (
    "You are Jarvis, a helpful home assistant. "
    "Answer clearly and concisely. When you use a tool, describe the result naturally."
)


@ws_router.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket) -> None:
    """Stream LLM responses over WebSocket for Open WebUI's real-time display."""
    await websocket.accept()
    log.info("ws_connected", client=websocket.client)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"type": "error", "error": "invalid JSON"}))
                continue

            message: str = payload.get("message", "").strip()
            if not message:
                continue

            user_id: str = payload.get("user_id", "webui_user")
            thread_id: str = payload.get("thread_id") or str(uuid.uuid4())
            history: list[dict] = payload.get("history") or []

            # Build message list: system + recent history + new user message
            messages: list[dict] = [{"role": "system", "content": _SYSTEM_PROMPT}]
            for h in history[-10:]:
                role = h.get("role", "user")
                content = h.get("content", "")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})
            messages.append({"role": "user", "content": message})

            try:
                async for chunk in _stream(messages, thread_id):
                    await websocket.send_text(json.dumps(chunk))
            except (httpx.HTTPError, httpx.StreamError) as exc:
                log.warning("ws_stream_error", error=str(exc))
                await websocket.send_text(
                    json.dumps({"type": "error", "error": "Stream failed — please retry."})
                )

    except WebSocketDisconnect:
        log.info("ws_disconnected")
    except Exception as exc:  # noqa: BLE001
        log.error("ws_unhandled_error", error=str(exc))


async def _stream(
    messages: list[dict], thread_id: str
) -> AsyncIterator[dict]:
    """Yield OpenAI-style streaming chunk dicts from LiteLLM."""
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            f"{settings.litellm_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.litellm_api_key}"},
            json={
                "model": settings.litellm_model,
                "messages": messages,
                "stream": True,
                "temperature": 0.3,
                "max_tokens": 800,
            },
        ) as response:
            response.raise_for_status()

            async for line in response.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    yield {"type": "done", "thread_id": thread_id}
                    return
                try:
                    obj = json.loads(data)
                    delta = obj["choices"][0]["delta"]
                    token = delta.get("content")
                    if token:
                        yield {"type": "chunk", "content": token, "thread_id": thread_id}
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
