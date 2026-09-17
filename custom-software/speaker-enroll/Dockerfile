FROM python:3.11-slim

# ffmpeg converts whatever the browser/phone sends (webm/opus recordings,
# mp4/aac video, mp3, ...) into the 16kHz mono WAV speechbrain-speaker-id
# actually decodes.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY static/ static/

ENV SPEECHBRAIN_URL=http://localhost:8200

EXPOSE 8201
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s \
    CMD curl -sf http://localhost:8201/ || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8201"]
