"""Qdrant-backed VectorStore for ScopedMemory.

Satisfies the VectorStore protocol defined in agent.memory.scoping and provides
a module-level singleton so routes and tasks share one client pool.

Embeddings are generated via the LiteLLM embeddings endpoint (nomic-embed-text:v1.5,
768 dimensions). Every call to search() or upsert() auto-creates the collection
on first use so there's nothing to pre-provision.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import structlog
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

from agent.config import get_settings

log = structlog.get_logger(__name__)

EMBEDDING_DIM = 768       # nomic-embed-text:v1.5 output dimension
DEFAULT_COLLECTION = "memories"


# ──────────────────────────────────────────────────────────────────────
# Embedding helper
# ──────────────────────────────────────────────────────────────────────


async def embed(text: str) -> list[float]:
    """Get a 768-dim embedding from LiteLLM (nomic-embed-text:v1.5)."""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{settings.litellm_base_url}/embeddings",
            headers={"Authorization": f"Bearer {settings.litellm_api_key}"},
            json={"model": settings.embeddings_model, "input": text},
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]


# ──────────────────────────────────────────────────────────────────────
# Filter translation
# ──────────────────────────────────────────────────────────────────────


def _to_qdrant_filter(scope_filter: dict[str, Any]) -> Filter | None:
    """Translate ScopedMemory.build_filter() output into a Qdrant Filter.

    ScopedMemory produces two shapes:
      * {"must": [{"key": k, "match": {"value": v}}, ...]}
      * {"should": [<clause>, ...]}   where each clause is a {"must": [...]}
    """
    if not scope_filter:
        return None

    def leaf(cond: dict[str, Any]) -> FieldCondition:
        return FieldCondition(key=cond["key"], match=MatchValue(value=cond["match"]["value"]))

    def parse(clause: dict[str, Any]) -> Filter:
        if "must" in clause:
            return Filter(must=[leaf(c) for c in clause["must"]])
        if "should" in clause:
            return Filter(should=[parse(c) for c in clause["should"]])
        # Bare leaf (shouldn't appear but handle gracefully)
        return Filter(must=[leaf(clause)])

    return parse(scope_filter)


# ──────────────────────────────────────────────────────────────────────
# Store implementation
# ──────────────────────────────────────────────────────────────────────


class QdrantMemoryStore:
    """VectorStore implementation backed by Qdrant, satisfying the Protocol in scoping.py."""

    def __init__(
        self,
        qdrant_url: str,
        collection: str = DEFAULT_COLLECTION,
    ) -> None:
        self.client = AsyncQdrantClient(url=qdrant_url)
        self.collection = collection
        self._initialized = False

    async def _ensure_collection(self) -> None:
        if self._initialized:
            return
        existing = {c.name for c in (await self.client.get_collections()).collections}
        if self.collection not in existing:
            await self.client.create_collection(
                self.collection,
                vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
            )
            log.info("qdrant_collection_created", collection=self.collection)
        self._initialized = True

    async def search(
        self,
        query: str,
        scope_filter: dict[str, Any],
        limit: int,
    ) -> list[dict[str, Any]]:
        await self._ensure_collection()
        vector = await embed(query)
        results = await self.client.search(
            self.collection,
            query_vector=vector,
            query_filter=_to_qdrant_filter(scope_filter),
            limit=limit,
            with_payload=True,
        )
        return [
            {"id": str(r.id), "payload": r.payload or {}, "score": r.score}
            for r in results
        ]

    async def upsert(self, memory_id: str, text: str, payload: dict[str, Any]) -> str:
        await self._ensure_collection()
        vector = await embed(text)
        # Qdrant UUIDs must be strings in uuid4 format
        try:
            point_id = str(uuid.UUID(memory_id))
        except ValueError:
            point_id = str(uuid.uuid5(uuid.NAMESPACE_OID, memory_id))

        await self.client.upsert(
            self.collection,
            points=[PointStruct(id=point_id, vector=vector, payload=payload)],
        )
        return point_id

    async def set_payload(self, memory_id: str, payload: dict[str, Any]) -> bool:
        try:
            point_id = str(uuid.UUID(memory_id))
        except ValueError:
            point_id = str(uuid.uuid5(uuid.NAMESPACE_OID, memory_id))
        await self.client.set_payload(
            self.collection,
            payload=payload,
            points=[point_id],
        )
        return True


# ──────────────────────────────────────────────────────────────────────
# Module-level singleton
# ──────────────────────────────────────────────────────────────────────

_store: QdrantMemoryStore | None = None


def get_store() -> QdrantMemoryStore:
    """Return the shared QdrantMemoryStore, creating it on first call."""
    global _store
    if _store is None:
        s = get_settings()
        _store = QdrantMemoryStore(
            qdrant_url=f"http://{s.qdrant_host}:{s.qdrant_port}",
            collection=s.memory_collection,
        )
    return _store
