"""Wyoming STT proxy that taps audio for speaker identification.

Sits transparently between HA and the real Wyoming Whisper server: relays
the ASR protocol (describe/info, transcribe/audio-start/audio-chunk*/
audio-stop -> transcript) unchanged, while also accumulating the raw PCM
audio per connection and, once the utterance ends, running
speechbrain-speaker-id's /identify on it in the background.

Why a single "last speaker" slot instead of per-satellite tracking:
confirmed against home-assistant/core's actual wyoming/stt.py --
HA's Wyoming STT call only ever sends `Transcribe(language=...)`. No
device_id, no satellite identifier, nothing -- every STT call looks
identical to whatever sits at this protocol layer, and HA opens a fresh
TCP connection per call regardless of which satellite triggered it. The
device_id HA does know gets attached later, downstream, when the transcribed
text reaches the conversation agent -- a layer this proxy never sees.
So the only signal available here is time: agent-orchestrator (which does
get device_id via ha_jarvis) reads GET /last-speaker and treats "identified
within the last few seconds" as a same-utterance match. This is Option A1:
accepted tradeoff is a same-second cross-satellite mixup being possible,
in exchange for not needing N proxy instances + N separate HA pipelines
(Option A2).
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import time
import wave

import httpx
from aiohttp import web
from wyoming.audio import AudioChunk, AudioStart
from wyoming.client import AsyncTcpClient
from wyoming.event import Event
from wyoming.server import AsyncEventHandler, AsyncServer

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("wyoming-identify-proxy")

BACKEND_HOST = os.environ.get("BACKEND_HOST", "wyoming-whisper")
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "10300"))
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "10300"))
SPEECHBRAIN_URL = os.environ.get("SPEECHBRAIN_URL", "http://speechbrain:8200").rstrip("/")
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8300"))
LAST_SPEAKER_TTL = float(os.environ.get("LAST_SPEAKER_TTL", "8.0"))

# {"user_id": str, "confidence": float, "ts": float} | None -- single global
# slot, deliberately not keyed by anything (see module docstring).
_last_speaker: dict | None = None


async def _run_identify(audio: bytes, rate: int, width: int, channels: int) -> None:
    global _last_speaker
    if not audio:
        return

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(audio)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{SPEECHBRAIN_URL}/identify",
                files={"audio": ("utterance.wav", buf.getvalue(), "audio/wav")},
            )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        log.warning("identify_failed error=%s", exc)
        return

    user_id = data.get("user_id")
    if user_id and user_id != "unknown":
        _last_speaker = {
            "user_id": user_id,
            "confidence": data.get("confidence", 0.0),
            "ts": time.monotonic(),
        }
        log.info("identified speaker=%s confidence=%.3f", user_id, data.get("confidence", 0.0))
    else:
        log.info("identify_no_match confidence=%s", data.get("confidence"))


class ProxyHandler(AsyncEventHandler):
    """One instance per HA connection -- mirrors HA's own one-call-per-connection pattern."""

    def __init__(self, reader, writer) -> None:
        super().__init__(reader, writer)
        self._backend: AsyncTcpClient | None = None
        self._audio = bytearray()
        self._rate = 16000
        self._width = 2
        self._channels = 1

    async def _backend_client(self) -> AsyncTcpClient:
        if self._backend is None:
            self._backend = AsyncTcpClient(BACKEND_HOST, BACKEND_PORT)
            await self._backend.connect()
        return self._backend

    async def handle_event(self, event: Event) -> bool:
        backend = await self._backend_client()

        if AudioStart.is_type(event.type):
            start = AudioStart.from_event(event)
            self._rate, self._width, self._channels = start.rate, start.width, start.channels
            self._audio.clear()
        elif AudioChunk.is_type(event.type):
            self._audio.extend(AudioChunk.from_event(event).audio)

        await backend.write_event(event)

        # Per the ASR protocol (and HA's own client), the backend only ever
        # replies once: to `describe` with `info`, and to the audio-chunk
        # stream's `audio-stop` with a single `transcript`. Everything else
        # is fire-and-forget.
        if event.type in ("describe", "audio-stop"):
            reply = await backend.read_event()
            if reply is not None:
                await self.write_event(reply)

            if event.type == "audio-stop":
                asyncio.create_task(
                    _run_identify(bytes(self._audio), self._rate, self._width, self._channels)
                )
                await backend.disconnect()
                self._backend = None
                return False  # HA closes its side right after the transcript anyway

        return True

    async def disconnect(self) -> None:
        if self._backend is not None:
            await self._backend.disconnect()
            self._backend = None


async def _http_last_speaker(request: web.Request) -> web.Response:
    if _last_speaker is None or (time.monotonic() - _last_speaker["ts"]) > LAST_SPEAKER_TTL:
        return web.json_response({"user_id": None})
    return web.json_response(
        {
            "user_id": _last_speaker["user_id"],
            "confidence": _last_speaker["confidence"],
            "age_seconds": round(time.monotonic() - _last_speaker["ts"], 2),
        }
    )


async def _http_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def _run_http_server() -> None:
    app = web.Application()
    app.router.add_get("/last-speaker", _http_last_speaker)
    app.router.add_get("/health", _http_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", HTTP_PORT)
    await site.start()
    log.info("HTTP side-channel listening on :%d", HTTP_PORT)


async def main() -> None:
    asyncio.create_task(_run_http_server())
    server = AsyncServer.from_uri(f"tcp://0.0.0.0:{LISTEN_PORT}")
    log.info(
        "Wyoming identify-proxy listening on :%d, backend=%s:%d, speechbrain=%s",
        LISTEN_PORT, BACKEND_HOST, BACKEND_PORT, SPEECHBRAIN_URL,
    )
    await server.run(ProxyHandler)


if __name__ == "__main__":
    asyncio.run(main())
