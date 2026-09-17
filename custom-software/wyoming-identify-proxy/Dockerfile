FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

ENV BACKEND_HOST=wyoming-whisper \
    BACKEND_PORT=10300 \
    LISTEN_PORT=10300 \
    SPEECHBRAIN_URL=http://speechbrain:8200 \
    HTTP_PORT=8300 \
    LAST_SPEAKER_TTL=8.0

EXPOSE 10300 8300
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s \
    CMD curl -sf http://localhost:8300/health || exit 1

CMD ["python", "app.py"]
