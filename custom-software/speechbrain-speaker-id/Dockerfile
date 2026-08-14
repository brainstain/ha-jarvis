FROM python:3.11-slim

# SpeechBrain ECAPA-TDNN speaker identification service.
# API per servers/inference/SERVER_SPEC.md:
#   POST /enroll    audio + user_id -> stores embedding in Qdrant
#   POST /identify  audio -> {user_id, confidence}
#   GET  /speakers  list enrolled speakers
#   GET  /health

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Model cache + speaker data
ENV HF_HOME=/data/models \
    DEVICE=cpu \
    QDRANT_HOST=localhost \
    QDRANT_PORT=6333 \
    SIMILARITY_THRESHOLD=0.7

EXPOSE 8200
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s \
    CMD curl -sf http://localhost:8200/health || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8200"]
