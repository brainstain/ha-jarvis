"""/ws/chat — the streaming endpoint Open WebUI's live-typing view uses."""

from __future__ import annotations

import asyncio
import json

import websockets

from conftest import require


def _ws_url(base_http_url: str) -> str:
    return base_http_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws/chat"


async def test_websocket_round_trip(endpoints, budgets, perf, reachability):
    require(reachability, "orchestrator", "litellm")
    url = _ws_url(endpoints.orchestrator_url)

    async def round_trip() -> str:
        async with websockets.connect(url, open_timeout=10) as ws:
            await ws.send(
                json.dumps({"message": "Reply with exactly one word: pong.", "user_id": "e2e-test-user"})
            )
            chunks: list[str] = []
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=25.0)
                event = json.loads(raw)
                if event["type"] == "error":
                    raise AssertionError(f"websocket returned an error: {event['error']}")
                if event["type"] == "done":
                    break
                chunks.append(event.get("content", ""))
            return "".join(chunks)

    reply, _ = await perf.atimed(
        "orchestrator/ws_chat_roundtrip", round_trip, budget=budgets.websocket_roundtrip
    )
    assert reply.strip()


async def test_websocket_rejects_malformed_json(endpoints, reachability):
    require(reachability, "orchestrator")
    url = _ws_url(endpoints.orchestrator_url)

    async with websockets.connect(url, open_timeout=10) as ws:
        await ws.send("not json")
        raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
        event = json.loads(raw)
        assert event["type"] == "error"
