# Server Spec: Agent & Worker Node (Mid Server)

**Hardware:** 8-core CPU, 64 GB RAM, GTX 1080 Ti (11 GB VRAM), 3 TB mixed storage  
**Role:** Agent orchestration, vector DB, chat UI, async workers, secondary inference  
**Always-on:** Yes  
**Proxmox VM:** `vm-agent` — 8 vCPU, 56 GB RAM, 500 GB NVMe, GPU passthrough (1080 Ti)

---

## Services

| Service | Image | Port | RAM | GPU | Purpose |
|---------|-------|------|-----|-----|---------|
| LiteLLM Proxy | ghcr.io/berriai/litellm | 4000 | 200 MB | — | Inference routing + failover |
| Ollama (secondary) | ollama/ollama | 11434 | 1 GB + model | 1080 Ti | 8B fallback + embeddings |
| Qdrant | qdrant/qdrant | 6333, 6334 | 2 GB | — | Vector DB (memory + RAG) |
| Agent Orchestrator | **custom** | 8100 | 1 GB | — | LangGraph agent core |
| Celery Worker | **custom** | — | 512 MB | — | Async task execution |
| Open WebUI | ghcr.io/open-webui/open-webui | 3000 | 500 MB | — | Chat interface |
| Perplexica | **self-built** | 3999 | 500 MB | — | AI-powered deep search |
| Playwright MCP | **custom** | 8101 | 300 MB | — | Browser automation |
| Prometheus | prom/prometheus | 9090 | 300 MB | — | Metrics collection |
| Grafana | grafana/grafana | 3100 | 200 MB | — | Dashboards + alerting |
| ollama-metrics | norskhelsenett/ollama-metrics | 9091 | 50 MB | — | Ollama Prometheus sidecar |
| nvidia_gpu_exporter | utkuozdemir/nvidia_gpu_exporter | 9835 | 30 MB | — | GPU metrics for both nodes |
| node-exporter | prom/node-exporter | 9100 | 30 MB | — | System metrics |

**Total estimated RAM:** ~7 GB base + models (~9 GB for 8B Q8 + embeddings)  
**Total with headroom:** ~18 GB (well within 56 GB VM allocation)

---

## Ollama Configuration (Secondary)

The 1080 Ti runs:
- **Qwen3-4B Q8_0** (~5 GB): Always loaded — used by meta-reasoning router for intent classification and tool selection
- **nomic-embed-text-v1.5 F16** (~0.3 GB): Always loaded — embeddings for Qdrant
- **Qwen3-8B Q8_0** (~9 GB): On-demand — failover when 3090 unavailable (swaps out 4B)

```bash
# Environment variables
OLLAMA_HOST=0.0.0.0
OLLAMA_MAX_LOADED_MODELS=2        # 4B router + embeddings simultaneously
OLLAMA_KEEP_ALIVE=24h             # Keep router model warm
OLLAMA_NUM_PARALLEL=2             # Concurrent requests
OLLAMA_FLASH_ATTENTION=1          # Not supported on 1080 Ti Pascal arch — will be ignored
NVIDIA_VISIBLE_DEVICES=all
```

### Model Pre-pull Script
```bash
#!/bin/bash
# Run after Ollama container starts
ollama pull qwen3:4b-q8_0
ollama pull nomic-embed-text:v1.5
ollama pull qwen3:8b-q8_0
```

---

## Qdrant Configuration

```yaml
# qdrant_config.yaml
storage:
  storage_path: /qdrant/storage
  on_disk_payload: true          # CRITICAL: prevents OOM during indexing
  wal:
    wal_capacity_mb: 64
    wal_segments_ahead: 0

service:
  grpc_port: 6334
  http_port: 6333
  enable_cors: true

# Recovery mode — set via env var QDRANT_ALLOW_RECOVERY_MODE=true
# Allows loading collection metadata only if OOM during normal startup
```

**Collections:**
- `memories` — Mem0-managed memories with scope/user_id metadata
- `documents` — RAG embeddings from Paperless-NGX with scope/doc_ref metadata
- `conversations` — Conversation history for search/retrieval

**Storage:** Must be on **local NVMe** — NFS is unsupported for Qdrant.

---

## LiteLLM Proxy Configuration

Single inference endpoint for all consumers. See `config/litellm_config.yaml` for full routing config.

**Consumers that point to LiteLLM:**
- Agent Orchestrator → `http://localhost:4000/v1`
- Open WebUI → `http://localhost:4000/v1` (configured as OpenAI-compatible endpoint)
- Home Assistant → configured via custom conversation agent that calls orchestrator

**Key behaviors:**
- Priority routing: 3090 (priority 1) → 1080 Ti (priority 2)
- 2 retries per backend before failover
- 60s cooldown on failed backends
- Health checks every 10s

---

## Agent Orchestrator

**Custom software** — see `custom-software/agent-orchestrator/` for full spec.

Exposes:
- `POST /chat` — synchronous chat (for voice pipeline, Open WebUI)
- `POST /chat/async` — queue async task, returns task_id
- `GET /tasks/{task_id}` — poll async task status
- `POST /tasks/{task_id}/resume` — resume HITL-interrupted workflow
- `GET /tasks/pending` — list workflows awaiting user input
- `WebSocket /ws/chat` — streaming responses for Open WebUI

Connects to:
- LiteLLM Proxy (inference)
- Qdrant (memory + RAG)
- Redis on Gateway (Celery broker + caching)
- MCP servers (tool calling)

---

## Health Checks

| Service | Check | Interval | Start Period |
|---------|-------|----------|-------------|
| LiteLLM | HTTP /health | 15s | 30s |
| Ollama | HTTP /api/tags | 30s | 60s |
| Qdrant | HTTP /healthz | 10s | 10s |
| Agent Orchestrator | HTTP /health | 15s | 30s |
| Open WebUI | HTTP / | 30s | 30s |
| Prometheus | HTTP /-/healthy | 15s | 10s |
| Grafana | HTTP /api/health | 15s | 15s |

---

## Data Volumes (Persistent)

| Volume | Mount | Storage | Backup |
|--------|-------|---------|--------|
| qdrant_data | /qdrant/storage | Local NVMe | Daily snapshot → NAS |
| langgraph_data | /data/langgraph | Local NVMe | Hourly .backup → NAS |
| openwebui_data | /app/backend/data | Local NVMe | Daily rsync → NAS |
| ollama_models | /root/.ollama | Local NVMe | Manual (large files) |
| prometheus_data | /prometheus | Local SSD | Weekly rsync → NAS |
| grafana_data | /var/lib/grafana | Local SSD | Daily rsync → NAS |

---

## Firewall Rules

| From | To | Port | Purpose |
|------|-----|------|---------|
| Gateway (HA) | Agent Orchestrator | 8100 | Voice pipeline + HA conversation |
| Gateway (Redis) | Celery Worker | 6379 | Task queue |
| Agent (LiteLLM) | Inference (Ollama) | 11434 | Primary LLM inference |
| Agent (LiteLLM) | Agent (Ollama) | 11434 | Fallback inference |
| Browsers | Open WebUI | 3000 | Chat UI (via Caddy) |
| Prometheus | Inference (exporters) | 9100, 9835 | Scrape metrics |
| Agent Orchestrator | Gateway (SearXNG) | 8888 | Web search |
| Agent Orchestrator | NAS (Paperless) | 8000 | Document API |
