# Server Spec: Inference Engine (Power Server)

**Hardware:** 16-core CPU, 64 GB RAM, RTX 3090 (24 GB VRAM), 2 TB RAID 1 NVMe  
**Role:** Primary LLM inference, speech-to-text, text-to-speech, speaker ID  
**Always-on:** Yes  
**Proxmox VM:** `vm-inference` — 12 vCPU, 48 GB RAM, 500 GB NVMe, GPU passthrough (3090)  
**Last verified:** 2026-08-14

---

## Services

| Service | Image | Port | RAM | GPU VRAM | Purpose |
|---------|-------|------|-----|----------|---------|
| Ollama (primary) | ollama/ollama | 11434 | 2 GB | ~21 GB | Qwen3-30B-A3B primary reasoning |
| wyoming-whisper | rhasspy/wyoming-faster-whisper | 10300 | 1 GB | ~3 GB | Speech-to-text (Wyoming) |
| Piper TTS | rhasspy/wyoming-piper | 10200 | 300 MB | — (CPU) | Text-to-speech |
| SpeechBrain | **custom** (phase2) | 8200 | 1 GB | — (CPU) | Speaker identification |
| openWakeWord | rhasspy/wyoming-openwakeword | 10400 | 200 MB | — (CPU) | Wake word detection |
| ollama-metrics | ghcr.io/norskhelsenett/ollama-metrics | 9091 | 30 MB | — | Ollama Prometheus metrics |
| nvidia_gpu_exporter | utkuozdemir/nvidia_gpu_exporter | 9835 | 30 MB | — | GPU metrics |
| node-exporter | prom/node-exporter | 9100 | 30 MB | — | System metrics |

**Total estimated RAM:** ~5 GB system + models in VRAM  
**Total VRAM usage:** ~24 GB budget on a 24 GB card — see budget below; measure with `nvidia-smi` after full load and update this table with real numbers.

---

## Ollama Configuration (Primary)

The RTX 3090 runs the primary reasoning model:

- **Qwen3-30B-A3B Q5_K_M** (~20–21 GB VRAM in practice): Primary model for all agent reasoning, planning, and tool calling

```bash
# Environment variables
OLLAMA_HOST=0.0.0.0
OLLAMA_MAX_LOADED_MODELS=1        # Only one large model at a time
OLLAMA_KEEP_ALIVE=-1              # Never unload — always warm
OLLAMA_NUM_PARALLEL=4             # Concurrent request slots (KV cache is split across slots)
OLLAMA_FLASH_ATTENTION=1          # Ampere supports flash attention
NVIDIA_VISIBLE_DEVICES=all
```

### VRAM Budget

| Consumer | VRAM | Notes |
|----------|------|-------|
| Qwen3-30B-A3B Q5_K_M | ~20–21 GB | Primary model, always loaded (18 GB was optimistic) |
| faster-whisper large-v3-turbo | ~3 GB | Shared GPU, always loaded |
| KV cache + CUDA context overhead | ~1–2 GB | 4 parallel slots; each CUDA process adds ~300–500 MB context |
| **Total** | **~24 GB+** | Over budget at Q5 — see below |

**SpeechBrain runs on CPU by default** (freed ~1 GB VRAM; adds ~2 s to enrollment/ID latency, acceptable for speaker ID). Even so, Q5_K_M plus whisper is borderline on 24 GB.

Remaining options if OOM occurs in practice:
1. Drop to Q4_K_M quantization (~15–17 GB, ~5% quality loss) — **recommended first move**
2. Use whisper medium instead of large-v3-turbo (~1.5 GB savings)
3. Reduce OLLAMA_NUM_PARALLEL to 2 (smaller KV allocation)

### Model Pre-pull Script
```bash
#!/bin/bash
# Verify the tag exists in the Ollama library before relying on it in automation.
ollama pull qwen3:30b-a3b-q5_K_M
```

---

## Voice Pipeline Services

### wyoming-faster-whisper (STT)

Uses `rhasspy/wyoming-faster-whisper` so Home Assistant's Wyoming integration can connect natively (the previously specified `fedirz/faster-whisper-server` speaks OpenAI-style REST, not Wyoming, and has been renamed upstream to *speaches* — it cannot serve HA's assist pipeline).

```yaml
# Config (container args)
model: large-v3-turbo      # via --model
device: cuda               # GPU inference
compute_type: float16
language: en
beam_size: 5
uri: tcp://0.0.0.0:10300   # Standard Wyoming STT port
```

Receives audio from HA via the Wyoming protocol, returns text transcript.

### Piper TTS

```yaml
# Config
voice: en_US-lessac-medium    # Natural-sounding US English
length_scale: 1.0             # Speed (lower = faster speech)
noise_scale: 0.667            # Variation
noise_w: 0.8                  # Phoneme width noise
```

CPU-only — Piper is lightweight enough that GPU acceleration is unnecessary and preserves VRAM for inference.

### SpeechBrain Speaker Identification (Phase 2)

**Custom container** wrapping SpeechBrain's ECAPA-TDNN model for speaker verification. Build context: `custom-software/speechbrain-speaker-id/` (Dockerfile + FastAPI wrapper — must exist before enabling `--profile phase2`). Runs on **CPU** by default to preserve VRAM (`DEVICE=cpu`).

**Enrollment flow:**
1. User records 3-5 voice samples via Open WebUI or HA
2. SpeechBrain generates speaker embedding (192-dim vector)
3. Embedding stored in Qdrant `speakers` collection (Qdrant runs on the **Agent node**, port 6333) with user_id metadata
4. At runtime: incoming audio → embedding → cosine similarity against enrolled speakers
5. Returns `user_id` if similarity > 0.7 threshold, else `unknown`

**API:**
- `POST /enroll` — body: audio file + user_id → stores embedding
- `POST /identify` — body: audio file → returns `{user_id, confidence}`
- `GET /speakers` — list enrolled speakers
- `GET /health` — health check

### openWakeWord

Server-side wake word confirmation. ESP32 satellites run microWakeWord locally for fast response; the server-side model confirms to reduce false positives.

```yaml
# Config
preloaded_models:
  - hey_jarvis            # bundled default model; ok_jarvis is NOT bundled
                          # (custom-trained models can be mounted and preloaded later)
threshold: 0.5
trigger_level: 3          # Require 3 consecutive frames above threshold
```

---

## GPU Passthrough (VM + VFIO)

The 3090 is passed to `vm-inference` via **VFIO/IOMMU passthrough** on the Proxmox host; the **NVIDIA Container Toolkit runs inside the guest VM** so containers get GPU access. This combines VM isolation with container-level resilience:

- Containers can crash and restart without GPU state corruption (Container Toolkit layer)
- A driver-level fault needs only a **VM** reboot, never a Proxmox host reboot
- The Proxmox host itself never loads NVIDIA drivers (GPU is bound to `vfio-pci`)

**Host (Proxmox) prerequisites:** IOMMU enabled in BIOS and kernel cmdline (`intel_iommu=on iommu=pt`), GPU + audio function bound to `vfio-pci` via `/etc/modprobe.d/vfio.conf`, vfio modules in the initramfs.

**Guest (Ubuntu VM) setup:**
- NVIDIA driver installed via `ubuntu-drivers` with **DKMS** so kernel updates recompile the module automatically
- `nvidia-persistenced` **enabled** — keeps the GPU initialized between container restarts (avoids first-request latency spikes)
- Hold/stage driver package updates (`apt-mark hold` or phased unattended-upgrades) so an automatic driver bump can't break the container toolkit mid-flight

```bash
# /etc/docker/daemon.json inside the guest VM (written by nvidia-ctk)
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

**Operational note:** GPU passthrough **pins vm-inference to this Proxmox node** — it cannot live-migrate or fail over to the other cluster node. Recovery plan: restore from vzdump backup + re-pull models.

### Thermal Management

The RTX 3090 thermal-throttles at ~83°C. Under sustained inference:
- Stock cooler: reaches 83°C within 10 minutes
- Recommended: aftermarket cooler or open-air case with directed airflow
- Prometheus alerts at 80°C (warning) and 85°C (critical) — see `config/alerts.yml`
- Emergency response: **GPU power-limit cap** via `scripts/gpu-thermal-cap.sh` (`nvidia-smi -pl`), triggered from Alertmanager. Power capping is instant, requires no container restart, and drops no in-flight requests — unlike the previously specified `OLLAMA_NUM_PARALLEL=1` change, which requires a restart and a 30–90 s model reload.

---

## Health Checks

| Service | Check | Interval | Start Period |
|---------|-------|----------|-------------|
| Ollama | `ollama list` (CLI ships in image; curl does not) | 30s | 240s |
| wyoming-whisper | python3 TCP probe :10300 | 30s | 60s |
| Piper TTS | python3 TCP probe :10200 | 15s | 15s |
| SpeechBrain | `curl http://localhost:8200/health` (curl included in custom image) | 30s | 30s |
| openWakeWord | python3 TCP probe :10400 | 15s | 10s |

**Notes:**
- Wyoming images don't reliably ship `nc`/`curl`; they do ship python3, so TCP probes use `python3 -c "socket.create_connection(...)"`.
- Ollama start_period is 240s: true cold start (first pull + 20 GB load from NVMe into VRAM) can exceed the previous 120s.

---

## Data Volumes

| Volume | Mount | Storage | Backup |
|--------|-------|---------|--------|
| ollama_models | /root/.ollama | Local NVMe | Not backed up (re-pull; excluded from vzdump) |
| whisper_cache | /root/.cache | Local NVMe | Not needed (re-download) |
| speechbrain_data | /data/speakers | Local NVMe | Daily → NAS (`scripts/backup-cron.sh`, inference section) |
| piper_voices | /data | Local NVMe | Not needed (re-download) |

**VM-level:** vzdump backup of vm-inference on the cluster's NFS backup storage. Exclude the ollama_models disk/volume from backup (large, fully re-pullable).

---

## Firewall Rules

Enforced via **ufw inside the guest VM** — see `servers/inference/setup-firewall.sh`. Ollama has no built-in auth; until LiteLLM is the only exposed path, ufw is the sole thing preventing arbitrary LAN clients from using or deleting models.

| From | To | Port | Purpose |
|------|-----|------|---------|
| Agent (LiteLLM) | Ollama | 11434 | LLM inference |
| Gateway (HA) | wyoming-whisper | 10300 | STT via Wyoming |
| Gateway (HA) | Piper TTS | 10200 | TTS via Wyoming |
| Gateway (HA) | openWakeWord | 10400 | Wake word via Wyoming |
| Agent (Orchestrator) | SpeechBrain | 8200 | Speaker identification |
| SpeechBrain (this host) | Agent (Qdrant) | 6333 | Speaker embedding storage (egress) |
| Agent (Prometheus) | ollama-metrics | 9091 | Ollama metrics |
| Agent (Prometheus) | node-exporter | 9100 | System metrics |
| Agent (Prometheus) | nvidia_gpu_exporter | 9835 | GPU metrics |
