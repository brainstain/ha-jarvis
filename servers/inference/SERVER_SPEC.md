# Server Spec: Inference Engine (Power Server)

**Hardware:** 16-core CPU, 64 GB RAM, RTX 3090 (24 GB VRAM), 2 TB RAID 1 NVMe  
**Role:** Primary LLM inference, speech-to-text, text-to-speech, speaker ID  
**Always-on:** Yes  
**Proxmox VM:** `vm-inference` — 12 vCPU, 48 GB RAM, 500 GB NVMe, GPU passthrough (3090)

---

## Services

| Service | Image | Port | RAM | GPU VRAM | Purpose |
|---------|-------|------|-----|----------|---------|
| Ollama (primary) | ollama/ollama | 11434 | 2 GB | ~18 GB | Qwen3-30B-A3B primary reasoning |
| faster-whisper | fedirz/faster-whisper-server | 8443 | 1 GB | ~3 GB | Speech-to-text |
| Piper TTS | rhasspy/wyoming-piper | 10200 | 300 MB | — (CPU) | Text-to-speech |
| SpeechBrain | **custom** | 8200 | 1 GB | ~1 GB | Speaker identification |
| openWakeWord | rhasspy/wyoming-openwakeword | 10400 | 200 MB | — (CPU) | Wake word detection |
| nvidia_gpu_exporter | utkuozdemir/nvidia_gpu_exporter | 9835 | 30 MB | — | GPU metrics |
| node-exporter | prom/node-exporter | 9100 | 30 MB | — | System metrics |

**Total estimated RAM:** ~5 GB system + models in VRAM  
**Total VRAM usage:** ~22 GB of 24 GB (Qwen3-30B ~18 GB + whisper ~3 GB + SpeechBrain ~1 GB)

---

## Ollama Configuration (Primary)

The RTX 3090 runs the primary reasoning model:

- **Qwen3-30B-A3B Q5_K_M** (~18 GB VRAM): Primary model for all agent reasoning, planning, and tool calling

```bash
# Environment variables
OLLAMA_HOST=0.0.0.0
OLLAMA_MAX_LOADED_MODELS=1        # Only one large model at a time
OLLAMA_KEEP_ALIVE=-1              # Never unload — always warm
OLLAMA_NUM_PARALLEL=4             # Concurrent requests (vLLM-style batching)
OLLAMA_FLASH_ATTENTION=1          # Ampere supports flash attention
NVIDIA_VISIBLE_DEVICES=all
```

### VRAM Budget

| Consumer | VRAM | Notes |
|----------|------|-------|
| Qwen3-30B-A3B Q5_K_M | ~18 GB | Primary model, always loaded |
| faster-whisper large-v3-turbo | ~3 GB | Shared GPU, always loaded |
| SpeechBrain speaker ID | ~1 GB | Shared GPU, always loaded |
| KV cache overhead | ~2 GB | For 4 concurrent requests |
| **Total** | **~24 GB** | Tight fit — monitor closely |

If VRAM pressure becomes an issue, options:
1. Drop to Q4_K_M quantization (~15 GB, ~5% quality loss)
2. Move SpeechBrain to CPU (slower enrollment, ~2s latency increase)
3. Use whisper medium instead of large (~1.5 GB savings)

### Model Pre-pull Script
```bash
#!/bin/bash
ollama pull qwen3:30b-a3b-q5_K_M
```

---

## Voice Pipeline Services

### faster-whisper (STT)

```yaml
# Config
model: large-v3-turbo
device: cuda
compute_type: float16
language: en
beam_size: 5
vad_filter: true          # Voice activity detection — skip silence
```

Receives audio from HA Wyoming protocol, returns text transcript. VAD filtering reduces unnecessary inference on silence/noise.

### Piper TTS

```yaml
# Config
voice: en_US-lessac-medium    # Natural-sounding US English
speaker: 0
length_scale: 1.0             # Speed (lower = faster speech)
noise_scale: 0.667            # Variation
noise_w: 0.8                  # Phoneme width noise
```

CPU-only — Piper is lightweight enough that GPU acceleration is unnecessary and preserves VRAM for inference.

### SpeechBrain Speaker Identification

**Custom container** wrapping SpeechBrain's ECAPA-TDNN model for speaker verification.

**Enrollment flow:**
1. User records 3-5 voice samples via Open WebUI or HA
2. SpeechBrain generates speaker embedding (192-dim vector)
3. Embedding stored in Qdrant `speakers` collection with user_id metadata
4. At runtime: incoming audio → embedding → cosine similarity against enrolled speakers
5. Returns `user_id` if similarity > 0.7 threshold, else `unknown`

**API:**
- `POST /enroll` — body: audio file + user_id → stores embedding
- `POST /identify` — body: audio file → returns `{user_id, confidence}`
- `GET /speakers` — list enrolled speakers

### openWakeWord

Server-side wake word confirmation. ESP32 satellites run microWakeWord locally for fast response; the server-side model confirms to reduce false positives.

```yaml
# Config
preloaded_models:
  - hey_jarvis
  - ok_jarvis
threshold: 0.5
trigger_level: 3          # Require 3 consecutive frames above threshold
```

---

## GPU Passthrough Notes

Using **NVIDIA Container Toolkit** (not VFIO passthrough) for resilience:
- Containers can crash and restart without GPU state corruption
- No full host reboot needed on container failure
- DKMS installed for automatic driver recompilation after kernel updates

```bash
# /etc/docker/daemon.json on Proxmox host
{
  "runtimes": {
    "nvidia": {
      "path": "nvidia-container-runtime",
      "runtimeArgs": []
    }
  },
  "default-runtime": "nvidia",
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
```

### Thermal Management

The RTX 3090 thermal-throttles at ~83°C. Under sustained inference:
- Stock cooler: reaches 83°C within 10 minutes
- Recommended: aftermarket cooler or open-air case with directed airflow
- Prometheus alert at 80°C (warning), 85°C (critical — throttling active)
- Emergency: automated `OLLAMA_NUM_PARALLEL=1` reduction at 85°C to lower load

---

## Health Checks

| Service | Check | Interval | Start Period |
|---------|-------|----------|-------------|
| Ollama | `curl http://localhost:11434/api/tags` | 30s | 120s |
| faster-whisper | `curl http://localhost:8443/health` | 30s | 60s |
| Piper TTS | TCP :10200 | 15s | 10s |
| SpeechBrain | `curl http://localhost:8200/health` | 30s | 30s |
| openWakeWord | TCP :10400 | 15s | 10s |

**Note:** Ollama start_period is 120s because initial model load on cold start (loading 18 GB from NVMe into VRAM) takes 30-90 seconds.

---

## Data Volumes

| Volume | Mount | Storage | Backup |
|--------|-------|---------|--------|
| ollama_models | /root/.ollama | Local NVMe | Manual (large) |
| whisper_models | /root/.cache | Local NVMe | Not needed (re-download) |
| speechbrain_data | /data/speakers | Local NVMe | Daily → NAS |
| piper_voices | /data/voices | Local NVMe | Not needed (re-download) |

---

## Firewall Rules

| From | To | Port | Purpose |
|------|-----|------|---------|
| Agent (LiteLLM) | Ollama | 11434 | LLM inference |
| Gateway (HA) | faster-whisper | 8443 | STT via Wyoming |
| Gateway (HA) | Piper TTS | 10200 | TTS via Wyoming |
| Gateway (HA) | openWakeWord | 10400 | Wake word via Wyoming |
| Agent (Orchestrator) | SpeechBrain | 8200 | Speaker identification |
| Agent (Prometheus) | node-exporter | 9100 | System metrics |
| Agent (Prometheus) | nvidia_gpu_exporter | 9835 | GPU metrics |
