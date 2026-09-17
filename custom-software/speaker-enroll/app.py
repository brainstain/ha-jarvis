"""Speaker enrollment UI — thin web front-end for speechbrain-speaker-id.

Serves a mobile-friendly page for recording/uploading a voice sample and
naming it, converts whatever audio it receives to the 16kHz mono WAV
speechbrain-speaker-id actually decodes (browser MediaRecorder output is
webm/ogg opus; phone video is mp4/aac — neither is a format speechbrain
decodes directly), and proxies enroll/list/delete/config calls server-side
so the browser never needs to reach speechbrain directly (avoids exposing
a second internal service, and sidesteps mixed-content/CORS entirely since
only this page's own origin is ever called from JS).
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("speaker-enroll")

SPEECHBRAIN_URL = os.environ.get("SPEECHBRAIN_URL", "http://localhost:8200").rstrip("/")
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="speaker-enroll")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict:
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(f"{SPEECHBRAIN_URL}/health")
            resp.raise_for_status()
            return {"status": "ok", "speechbrain": resp.json()}
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail=f"speechbrain unreachable: {exc}")


async def _to_wav(src_path: Path, dst_path: Path) -> None:
    """Convert any ffmpeg-readable audio/video to 16kHz mono WAV."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", str(src_path),
        "-ar", "16000", "-ac", "1", "-f", "wav", str(dst_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise HTTPException(
            status_code=400,
            detail=f"Could not convert audio: {stderr.decode(errors='replace')[-500:]}",
        )


@app.post("/enroll")
async def enroll(name: str = Form(...), audio: UploadFile = File(...)) -> dict:
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")

    raw = await audio.read()
    if not raw:
        raise HTTPException(status_code=400, detail="No audio received")

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / (audio.filename or "upload")
        dst = Path(tmp) / "converted.wav"
        src.write_bytes(raw)
        await _to_wav(src, dst)

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{SPEECHBRAIN_URL}/enroll",
                data={"user_id": name},
                files={"audio": ("sample.wav", dst.read_bytes(), "audio/wav")},
            )
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


@app.get("/speakers")
async def speakers() -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{SPEECHBRAIN_URL}/speakers")
    resp.raise_for_status()
    return resp.json()


@app.delete("/speakers/{user_id}")
async def delete_speaker(user_id: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.delete(f"{SPEECHBRAIN_URL}/speakers/{user_id}")
    resp.raise_for_status()
    return resp.json()


class ThresholdUpdate(BaseModel):
    threshold: float = Field(ge=0.0, le=1.0)


@app.get("/config")
async def get_config() -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{SPEECHBRAIN_URL}/config")
    resp.raise_for_status()
    return resp.json()


@app.post("/config")
async def set_config(update: ThresholdUpdate) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{SPEECHBRAIN_URL}/config", json=update.model_dump())
    resp.raise_for_status()
    return resp.json()
