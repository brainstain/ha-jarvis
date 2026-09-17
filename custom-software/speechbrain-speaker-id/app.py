"""SpeechBrain ECAPA-TDNN speaker identification service.

API per servers/inference/SERVER_SPEC.md:
  POST /enroll    audio file + user_id  -> stores 192-dim embedding in Qdrant
  POST /identify  audio file            -> {user_id, confidence} or unknown
  GET  /speakers  list enrolled speakers
  GET  /health    health check

Embeddings live in the Qdrant `speakers` collection on the Agent node;
matching is cosine similarity against enrolled speakers with a
configurable threshold (default 0.7).
"""

import io
import json
import os
import uuid
import logging
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import torchaudio
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm
from speechbrain.inference.speaker import EncoderClassifier

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("speechbrain-speaker-id")

QDRANT_HOST = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
DEVICE = os.environ.get("DEVICE", "cpu")
COLLECTION = "speakers"
EMBEDDING_DIM = 192  # ECAPA-TDNN output
TARGET_SR = 16000

# The threshold is runtime-configurable (POST /config), not just an env var:
# tune it after seeing real confidence scores rather than needing a redeploy.
# Persisted to the same mounted volume as the Qdrant-independent state, so
# it survives a container restart; the env var is only the first-ever default.
_CONFIG_PATH = Path("/data/speakers/config.json")
_DEFAULT_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.7"))


def _load_threshold() -> float:
    try:
        return float(json.loads(_CONFIG_PATH.read_text())["threshold"])
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
        return _DEFAULT_THRESHOLD


def _save_threshold(value: float) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps({"threshold": value}))


SIMILARITY_THRESHOLD = _load_threshold()

app = FastAPI(title="speechbrain-speaker-id")

classifier: EncoderClassifier | None = None
qdrant: QdrantClient | None = None


@app.on_event("startup")
def startup() -> None:
    global classifier, qdrant
    log.info("Loading ECAPA-TDNN model (device=%s)...", DEVICE)
    classifier = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        run_opts={"device": DEVICE},
    )
    log.info("Connecting to Qdrant at %s:%s", QDRANT_HOST, QDRANT_PORT)
    qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    existing = {c.name for c in qdrant.get_collections().collections}
    if COLLECTION not in existing:
        qdrant.create_collection(
            collection_name=COLLECTION,
            vectors_config=qm.VectorParams(
                size=EMBEDDING_DIM, distance=qm.Distance.COSINE
            ),
        )
        log.info("Created Qdrant collection '%s'", COLLECTION)


def _embed(audio_bytes: bytes) -> np.ndarray:
    """Decode audio bytes -> mono 16 kHz -> ECAPA embedding (192-dim).

    Decoding via soundfile/libsndfile, not torchaudio.load(): current
    torchaudio (2.14, pulled in by speechbrain>=1.0's own pin) dropped its
    legacy sox/soundfile load backends in favor of a separate `torchcodec`
    package that isn't installed here, so torchaudio.load() raised
    "TorchCodec is required for load_with_torchcodec" on every call —
    confirmed live, the very first real enrollment attempt failed on it.
    soundfile is already a direct dependency and needs no extra package.
    Accepts WAV/FLAC/OGG (libsndfile's formats); does NOT decode AAC/MP3/M4A
    — convert those client-side first (e.g. `afconvert`/`ffmpeg` to WAV).
    """
    try:
        data, sr = sf.read(io.BytesIO(audio_bytes), dtype="float32", always_2d=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Could not decode audio: {exc}")
    waveform = torch.from_numpy(data.T)  # (frames, channels) -> (channels, frames)
    if waveform.shape[0] > 1:  # downmix to mono
        waveform = waveform.mean(dim=0, keepdim=True)
    if sr != TARGET_SR:
        waveform = torchaudio.functional.resample(waveform, sr, TARGET_SR)
    with torch.no_grad():
        emb = classifier.encode_batch(waveform)
    vec = emb.squeeze().cpu().numpy().astype(np.float32)
    if vec.shape != (EMBEDDING_DIM,):
        raise HTTPException(status_code=500, detail=f"Unexpected embedding shape {vec.shape}")
    return vec


@app.get("/health")
def health() -> dict:
    ok = classifier is not None and qdrant is not None
    if not ok:
        raise HTTPException(status_code=503, detail="not ready")
    return {"status": "ok", "device": DEVICE, "threshold": SIMILARITY_THRESHOLD}


@app.post("/enroll")
async def enroll(user_id: str = Form(...), audio: UploadFile = File(...)) -> dict:
    vec = _embed(await audio.read())
    point_id = str(uuid.uuid4())
    qdrant.upsert(
        collection_name=COLLECTION,
        points=[
            qm.PointStruct(
                id=point_id,
                vector=vec.tolist(),
                payload={"user_id": user_id},
            )
        ],
    )
    log.info("Enrolled sample %s for user_id=%s", point_id, user_id)
    return {"status": "enrolled", "user_id": user_id, "sample_id": point_id}


@app.post("/identify")
async def identify(audio: UploadFile = File(...)) -> dict:
    vec = _embed(await audio.read())
    response = qdrant.query_points(
        collection_name=COLLECTION,
        query=vec.tolist(),
        limit=1,
        with_payload=True,
    )
    hits = response.points
    if not hits or hits[0].score < SIMILARITY_THRESHOLD:
        confidence = float(hits[0].score) if hits else 0.0
        return {"user_id": "unknown", "confidence": confidence}
    return {
        "user_id": hits[0].payload.get("user_id", "unknown"),
        "confidence": float(hits[0].score),
    }


@app.get("/speakers")
def speakers() -> dict:
    users: dict[str, int] = {}
    offset = None
    while True:
        points, offset = qdrant.scroll(
            collection_name=COLLECTION,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for p in points:
            uid = (p.payload or {}).get("user_id", "unknown")
            users[uid] = users.get(uid, 0) + 1
        if offset is None:
            break
    return {
        "speakers": [
            {"user_id": uid, "samples": count} for uid, count in sorted(users.items())
        ]
    }


@app.delete("/speakers/{user_id}")
def delete_speaker(user_id: str) -> dict:
    """Remove every enrolled sample for a speaker (e.g. to redo a bad enrollment)."""
    qdrant.delete(
        collection_name=COLLECTION,
        points_selector=qm.FilterSelector(
            filter=qm.Filter(
                must=[qm.FieldCondition(key="user_id", match=qm.MatchValue(value=user_id))]
            )
        ),
    )
    log.info("Deleted all samples for user_id=%s", user_id)
    return {"status": "deleted", "user_id": user_id}


class ThresholdUpdate(BaseModel):
    threshold: float = Field(ge=0.0, le=1.0)


@app.get("/config")
def get_config() -> dict:
    return {"threshold": SIMILARITY_THRESHOLD}


@app.post("/config")
def set_config(update: ThresholdUpdate) -> dict:
    global SIMILARITY_THRESHOLD
    SIMILARITY_THRESHOLD = update.threshold
    _save_threshold(update.threshold)
    log.info("Similarity threshold updated to %s", update.threshold)
    return {"threshold": SIMILARITY_THRESHOLD}
