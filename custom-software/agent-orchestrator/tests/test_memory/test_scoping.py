"""Memory scoping: family/personal boundaries and auto-promotion."""

import pytest

from agent.api.schemas import Memory, MemoryState
from agent.memory.scoping import ScopedMemory


class FakeStore:
    def __init__(self, results=None):
        self.results = results or []
        self.last_filter = None
        self.upserted = []
        self.payload_updates = []

    async def search(self, query, scope_filter, limit):
        self.last_filter = scope_filter
        return self.results[:limit]

    async def upsert(self, memory_id, text, payload):
        self.upserted.append((memory_id, text, payload))
        return memory_id

    async def set_payload(self, memory_id, payload):
        self.payload_updates.append((memory_id, payload))
        return True


def test_family_scope_filter_excludes_personal():
    f = ScopedMemory.build_filter("michael", "family")
    assert f == {"must": [{"key": "scope", "match": {"value": "family"}}]}


def test_personal_scope_filter_includes_family_and_own():
    f = ScopedMemory.build_filter("michael", "personal")
    assert "should" in f
    assert len(f["should"]) == 2
    own = f["should"][1]["must"]
    assert {"key": "user_id", "match": {"value": "michael"}} in own


@pytest.mark.asyncio
async def test_search_applies_scope_filter():
    store = FakeStore(results=[{"id": "1", "payload": {"text": "trash day is Tuesday",
                                                       "scope": "family", "user_id": "michael"}}])
    memory = ScopedMemory(store)
    results = await memory.search("trash", "michael", "family", limit=5)

    assert store.last_filter == ScopedMemory.build_filter("michael", "family")
    assert results[0].text == "trash day is Tuesday"


@pytest.mark.asyncio
async def test_personal_memory_stays_personal_when_not_shared():
    store = FakeStore()
    memory = ScopedMemory(store)
    await memory.store_memory(
        Memory(text="my password hint is blue", user_id="michael", scope="personal"),
        "michael", "personal",
    )
    _, _, payload = store.upserted[0]
    assert payload["scope"] == "personal"
    assert payload["auto_promoted"] is False


@pytest.mark.asyncio
async def test_household_memory_auto_promotes_to_family():
    store = FakeStore()
    memory = ScopedMemory(store)
    await memory.store_memory(
        Memory(text="the water heater was serviced in May", user_id="michael", scope="personal"),
        "michael", "personal",
    )
    _, _, payload = store.upserted[0]
    assert payload["scope"] == "family"
    assert payload["auto_promoted"] is True


@pytest.mark.asyncio
async def test_auto_promotion_can_be_disabled():
    store = FakeStore()
    memory = ScopedMemory(store, auto_promote_family=False)
    await memory.store_memory(
        Memory(text="grocery run on Saturday", user_id="michael", scope="personal"),
        "michael", "personal",
    )
    assert store.upserted[0][2]["scope"] == "personal"


@pytest.mark.asyncio
async def test_family_scope_never_downgraded():
    store = FakeStore()
    memory = ScopedMemory(store)
    await memory.store_memory(
        Memory(text="anything", user_id="michael", scope="family"), "michael", "family"
    )
    assert store.upserted[0][2]["scope"] == "family"


@pytest.mark.asyncio
async def test_update_state_transition():
    store = FakeStore()
    memory = ScopedMemory(store)
    assert await memory.update_state("mem-1", MemoryState.CONFIRMED) is True
    assert store.payload_updates == [("mem-1", {"state": "confirmed"})]
