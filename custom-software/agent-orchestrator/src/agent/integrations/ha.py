"""Home Assistant conversation agent integration.

HA's custom conversation agent protocol sends POST requests to this endpoint
when the user speaks to the Assist pipeline and Jarvis is selected as the
conversation agent in HA Settings > Voice Assistants.

Configure in HA configuration.yaml:
  conversation:
    custom:
      - name: Jarvis
        id: jarvis
        base_url: http://agent.home.local:8100

HA sends its internal bearer token as Authorization: Bearer <ha_token>.
We trust the request because it arrives from the internal network; we do not
verify the token value (it changes per HA instance and we don't have a way
to obtain it at runtime without an integration).
"""

from __future__ import annotations

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from agent.config import get_settings

log = structlog.get_logger(__name__)

ha_router = APIRouter(prefix="/ha", tags=["home-assistant"])

_IDENTIFY_TIMEOUT = 3.0  # LAN call to wyoming-identify-proxy; fail open, never block the reply


async def _lookup_speaker() -> tuple[str | None, float | None]:
    """Check wyoming-identify-proxy's last-speaker slot.

    Returns (user_id, confidence), both None if unavailable, unmatched, or
    the proxy is unreachable/disabled -- this must never fail the request,
    only degrade to the pre-Phase-2 family-scope default.
    """
    settings = get_settings()
    if not settings.wyoming_identify_proxy_url:
        return None, None
    try:
        async with httpx.AsyncClient(timeout=_IDENTIFY_TIMEOUT) as client:
            resp = await client.get(f"{settings.wyoming_identify_proxy_url}/last-speaker")
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        log.warning("speaker_lookup_failed", error=str(exc))
        return None, None
    return data.get("user_id"), data.get("confidence")


# ──────────────────────────────────────────────────────────────────────
# HA conversation protocol schemas
# ──────────────────────────────────────────────────────────────────────


class _ConversationInput(BaseModel):
    text: str
    language: str = "en"
    conversation_id: str | None = None
    agent_id: str | None = None


class _SpeechPlain(BaseModel):
    speech: str
    extra_data: None = None


class _ConversationResult(BaseModel):
    speech: dict
    card: dict = {}
    language: str = "en"
    response_type: str = "action_done"


class _ConversationResponse(BaseModel):
    response: _ConversationResult
    conversation_id: str


# ──────────────────────────────────────────────────────────────────────
# Endpoint
# ──────────────────────────────────────────────────────────────────────


@ha_router.post("/conversation/process", response_model=_ConversationResponse)
async def ha_conversation(
    payload: _ConversationInput, request: Request
) -> _ConversationResponse:
    """Handle a Home Assistant Assist pipeline conversation intent.

    HA sends POST /ha/conversation/process with text + conversation_id.
    We run it through the orchestrator's /chat endpoint (synchronous) and
    translate the response back to HA's expected format.
    """
    # HA always sends Authorization: Bearer <token> — require it so stray
    # clients can't use this endpoint without going through HA.
    if not request.headers.get("Authorization", "").startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing HA bearer token")

    from agent.api.routes import chat
    from agent.api.schemas import ChatRequest

    # A confident, recent speechbrain match promotes the request to that
    # person's personal scope; otherwise (unmatched, unconfident, or the
    # proxy unreachable) it falls back to the original family-scope default.
    # speechbrain's own /identify already gates on the configurable
    # similarity threshold -- a non-null user_id here already cleared it.
    speaker_id, confidence = await _lookup_speaker()

    chat_req = ChatRequest(
        message=payload.text,
        user_id=speaker_id or "ha_user",
        scope="personal" if speaker_id else "family",
        speaker_id=speaker_id,
        thread_id=payload.conversation_id or None,
        source="voice",
        metadata={
            "language": payload.language,
            "agent_id": payload.agent_id,
            "speaker_confidence": confidence,
        },
    )

    try:
        result = await chat(chat_req)
    except HTTPException as exc:
        log.warning("ha_chat_error", status=exc.status_code, detail=exc.detail)
        return _error_response(payload.conversation_id or "")
    except Exception as exc:  # noqa: BLE001
        log.error("ha_chat_unhandled", error=str(exc))
        return _error_response(payload.conversation_id or "")

    return _ConversationResponse(
        response=_ConversationResult(
            speech={"plain": {"speech": result.message, "extra_data": None}},
            card={},
            language=payload.language,
            response_type="action_done",
        ),
        conversation_id=result.thread_id,
    )


@ha_router.get("/health")
async def ha_health() -> dict[str, str]:
    """Quick liveness check HA can poll."""
    return {"status": "ok"}


def _error_response(conversation_id: str) -> _ConversationResponse:
    return _ConversationResponse(
        response=_ConversationResult(
            speech={
                "plain": {
                    "speech": "Sorry, I'm having trouble right now. Please try again.",
                    "extra_data": None,
                }
            },
            card={},
            language="en",
            response_type="error",
        ),
        conversation_id=conversation_id,
    )
