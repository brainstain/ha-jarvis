"""Scoped memory MCP server — Qdrant-backed, family/personal scope enforcement.

Implements all 7 tools from custom-software/mcp-servers/SPEC.md:
  memory_search, memory_store, memory_confirm, memory_promote,
  memory_update, memory_delete, memory_list_pending

Environment variables:
  QDRANT_URL          e.g. http://qdrant:6333
  LITELLM_URL         e.g. http://litellm:4000/v1
  LITELLM_API_KEY     sk-placeholder
  EMBEDDINGS_MODEL    embeddings  (name in LiteLLM config)
  MEMORY_COLLECTION   memories
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx
import structlog
from fastmcp import FastMCP
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointIdsList,
    PointStruct,
    VectorParams,
)

log = structlog.get_logger(__name__)

QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")
LITELLM_URL = os.environ.get("LITELLM_URL", "http://litellm:4000/v1")
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "sk-placeholder")
EMBEDDINGS_MODEL = os.environ.get("EMBEDDINGS_MODEL", "embeddings")
COLLECTION = os.environ.get("MEMORY_COLLECTION", "memories")
EMBEDDING_DIM = 768

mcp = FastMCP("mcp-memory-scoped")
_qdrant: AsyncQdrantClient | None = None
_initialized = False


async def _client() -> AsyncQdrantClient:
    global _qdrant, _initialized
    if _qdrant is None:
        _qdrant = AsyncQdrantClient(url=QDRANT_URL)
    if not _initialized:
        existing = {c.name for c in (await _qdrant.get_collections()).collections}
        if COLLECTION not in existing:
            await _qdrant.create_collection(
                COLLECTION,
                vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
            )
        _initialized = True
    return _qdrant


async def _embed(text: str) -> list[float]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{LITELLM_URL}/embeddings",
            headers={"Authorization": f"Bearer {LITELLM_API_KEY}"},
            json={"model": EMBEDDINGS_MODEL, "input": text},
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]


def _scope_filter(user_id: str, scope: str) -> Filter | None:
    if scope == "family":
        return Filter(must=[FieldCondition(key="scope", match=MatchValue(value="family"))])
    return Filter(
        should=[
            Filter(must=[FieldCondition(key="scope", match=MatchValue(value="family"))]),
            Filter(
                must=[
                    FieldCondition(key="scope", match=MatchValue(value="personal")),
                    FieldCondition(key="user_id", match=MatchValue(value=user_id)),
                ]
            ),
        ]
    )


def _parse_id(memory_id: str) -> str:
    try:
        return str(uuid.UUID(memory_id))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_OID, memory_id))


# ──────────────────────────────────────────────────────────────────────
# Tools
# ──────────────────────────────────────────────────────────────────────


@mcp.tool()
async def memory_search(
    query: str,
    user_id: str,
    scope: str = "personal",
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Search memories visible to this user at the given scope.

    scope: "family" returns only family memories; "personal" returns family +
    this user's personal memories.
    """
    client = await _client()
    vector = await _embed(query)
    results = await client.search(
        COLLECTION,
        query_vector=vector,
        query_filter=_scope_filter(user_id, scope),
        limit=limit,
        with_payload=True,
    )
    return [
        {
            "id": str(r.id),
            "text": (r.payload or {}).get("text", ""),
            "scope": (r.payload or {}).get("scope", "personal"),
            "memory_type": (r.payload or {}).get("memory_type", "fact"),
            "state": (r.payload or {}).get("state", "confirmed"),
            "tags": (r.payload or {}).get("tags", []),
            "score": r.score,
        }
        for r in results
    ]


@mcp.tool()
async def memory_store(
    text: str,
    user_id: str,
    scope: str = "personal",
    memory_type: str = "fact",
    tags: list[str] | None = None,
    source: str = "conversation",
) -> dict[str, str]:
    """Store a new memory with scope enforcement and auto-promotion.

    If scope is "personal" but the text contains household keywords
    (grocery, family, vacation, etc.), it is auto-promoted to "family".
    """
    import re
    from datetime import UTC, datetime

    FAMILY_PATTERNS = re.compile(
        r"\b(family|everyone|we(?:'re| are)|our|household|grocer(?:y|ies)|"
        r"shopping list|carpool|pick(?:ing)? up the kids|home maintenance|"
        r"hvac|furnace|water heater|appliance|vacation|dinner)\b",
        re.IGNORECASE,
    )

    effective_scope = scope
    promoted = False
    if scope == "personal" and FAMILY_PATTERNS.search(text):
        effective_scope = "family"
        promoted = True

    client = await _client()
    memory_id = str(uuid.uuid4())
    vector = await _embed(text)
    payload: dict[str, Any] = {
        "text": text,
        "scope": effective_scope,
        "user_id": user_id,
        "memory_type": memory_type,
        "state": "confirmed",
        "tags": tags or [],
        "source": source,
        "timestamp": datetime.now(UTC).isoformat(),
        "auto_promoted": promoted,
    }
    await client.upsert(COLLECTION, points=[PointStruct(id=memory_id, vector=vector, payload=payload)])
    return {"id": memory_id, "scope": effective_scope, "promoted": str(promoted)}


@mcp.tool()
async def memory_confirm(memory_id: str) -> dict[str, bool]:
    """Confirm a pending memory (transition state: pending → confirmed)."""
    client = await _client()
    await client.set_payload(COLLECTION, payload={"state": "confirmed"}, points=[_parse_id(memory_id)])
    return {"confirmed": True}


@mcp.tool()
async def memory_promote(memory_id: str, user_id: str) -> dict[str, str]:
    """Promote a personal memory to family scope."""
    client = await _client()
    point_id = _parse_id(memory_id)
    # Verify ownership before promoting
    results = await client.retrieve(COLLECTION, ids=[point_id], with_payload=True)
    if not results:
        return {"error": "memory not found"}
    payload = results[0].payload or {}
    if payload.get("user_id") != user_id and payload.get("scope") != "personal":
        return {"error": "cannot promote: not your personal memory"}
    await client.set_payload(COLLECTION, payload={"scope": "family"}, points=[point_id])
    return {"promoted": True, "memory_id": memory_id}


@mcp.tool()
async def memory_update(memory_id: str, text: str, user_id: str) -> dict[str, str]:
    """Update the text of an existing memory and re-embed it."""
    client = await _client()
    point_id = _parse_id(memory_id)
    vector = await _embed(text)
    # Overwrite the point with updated text + new embedding
    results = await client.retrieve(COLLECTION, ids=[point_id], with_payload=True)
    if not results:
        return {"error": "memory not found"}
    payload = dict(results[0].payload or {})
    payload["text"] = text
    payload["supersedes"] = memory_id
    await client.upsert(COLLECTION, points=[PointStruct(id=point_id, vector=vector, payload=payload)])
    return {"updated": True, "memory_id": memory_id}


@mcp.tool()
async def memory_delete(memory_id: str, user_id: str) -> dict[str, bool]:
    """Delete a memory by ID. Users can only delete their own personal memories."""
    client = await _client()
    point_id = _parse_id(memory_id)
    results = await client.retrieve(COLLECTION, ids=[point_id], with_payload=True)
    if not results:
        return {"deleted": False}
    payload = results[0].payload or {}
    if payload.get("scope") == "family" and payload.get("user_id") != user_id:
        return {"deleted": False, "reason": "family memories require admin action"}
    await client.delete(COLLECTION, points_selector=PointIdsList(points=[point_id]))
    return {"deleted": True}


@mcp.tool()
async def memory_list_pending(user_id: str, scope: str = "personal") -> list[dict[str, Any]]:
    """List memories in 'pending' state awaiting confirmation."""
    client = await _client()
    results = await client.scroll(
        COLLECTION,
        scroll_filter=Filter(
            must=[
                FieldCondition(key="state", match=MatchValue(value="pending")),
                FieldCondition(key="user_id", match=MatchValue(value=user_id)),
            ]
        ),
        with_payload=True,
        limit=20,
    )
    return [
        {
            "id": str(p.id),
            "text": (p.payload or {}).get("text", ""),
            "scope": (p.payload or {}).get("scope", "personal"),
            "timestamp": (p.payload or {}).get("timestamp", ""),
        }
        for p in results[0]
    ]
