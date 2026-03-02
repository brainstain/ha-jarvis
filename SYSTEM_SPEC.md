# Offline-First Home AI Agent — System Specification

**Version:** 1.2  
**Date:** 2026-03-01  
**Status:** Architecture Complete — Ready for Implementation

---

## 1. System Overview

A voice-first, offline-capable AI agent distributed across a 4-node Proxmox homelab cluster. The system manages home automation via Home Assistant, maintains dual-mode memory (family/personal), performs document retrieval and web research, and is extensible via MCP (Model Context Protocol) servers.

### 1.1 Design Principles

1. **Offline-first**: All core functionality works without internet. Cloud/external LLMs only when explicitly requested.
2. **Voice-primary, multi-interface**: Voice satellites are the primary input; Open WebUI, HA notifications, and push notifications are secondary.
3. **MCP for all integrations**: New capabilities = new MCP server. No core agent changes required.
4. **Graceful degradation**: Every component has a fallback. 30B model → 8B → 3B → built-in HA intents.
5. **Memory scoping**: Family mode (shared context) and Personal mode (family + private context) enforced at every layer.

### 1.2 Hardware Inventory

| Role | Node | CPU | RAM | GPU | Storage | Always-On |
|------|------|-----|-----|-----|---------|-----------|
| Gateway | Mini PC | 4C | 16 GB | — | 512 GB SSD | Yes |
| Agent & Worker | Mid Server | 8C | 64 GB | GTX 1080 Ti (11 GB) | 3 TB mixed | Yes |
| Inference Engine | Power Server | 16C | 64 GB | RTX 3090 (24 GB) | 2 TB RAID 1 M.2 | Yes |
| Storage | Synology NAS | — | — | — | 16 TB | Yes |

### 1.3 Network Topology

All nodes on the same VLAN. Services communicate via hostname DNS (Pi-hole/CoreDNS on Gateway). External access through Caddy reverse proxy with Authentik SSO.

```
Internet
    │
    ▼
[Gateway: Mini PC] ── Caddy → Authentik SSO
    │
    ├──── [Agent: Mid Server] ── Agent Orchestrator, Open WebUI, Qdrant, Perplexica
    │
    ├──── [Inference: Power Server] ── Ollama/vLLM, faster-whisper, Piper TTS
    │
    └──── [NAS: Synology] ── Paperless-NGX, Document Storage, Backups
```

---

## 2. Service Inventory

### 2.1 Per-Server Service Map

**Gateway (Mini PC)**
- Caddy (reverse proxy, automatic HTTPS)
- Authentik (SSO, user profiles, MFA)
- Redis (caching, session management, task queue broker)
- Home Assistant (core automation)
- Pi-hole (local DNS)
- SearXNG (metasearch)
- Uptime Kuma (monitoring — separate from monitored services)
- NUT (UPS management)

**Agent & Worker Node (Mid Server)**
- LiteLLM Proxy (inference routing + failover)
- Ollama (secondary, 7B-14B models on 1080 Ti)
- Qdrant (vector database)
- nomic-embed-text via Ollama (embedding model)
- Agent Orchestrator (custom — LangGraph + MCP clients)
- Open WebUI (chat interface)
- Perplexica (AI-powered search)
- Celery Worker (async task execution)
- MCP Server Hub (filesystem, memory, calendar, notifications)
- Playwright (browser automation)

**Inference Engine (Power Server)**
- Ollama (primary, Qwen3-30B-A3B on 3090)
- vLLM (secondary, robust tool calling)
- faster-whisper (STT, large-v3-turbo)
- Piper TTS (text-to-speech)
- SpeechBrain (speaker identification)
- openWakeWord (server-side wake word detection)

**Synology NAS**
- Paperless-NGX + Paperless-AI (document management + RAG)
- NFS/SMB shares (media, documents, backups)
- Synology Hyper Backup (offsite backup target)

### 2.2 Service Dependencies

```
Voice Satellite → openWakeWord → faster-whisper → SpeechBrain → Agent Orchestrator
Agent Orchestrator → LiteLLM Proxy → Ollama (3090) | Ollama (1080 Ti) | CPU fallback
Agent Orchestrator → Qdrant (memory)
Agent Orchestrator → MCP Servers → Home Assistant | Calendar | SearXNG | Filesystem | ...
Agent Orchestrator → Celery + Redis (async tasks)
Agent Orchestrator → Piper TTS → Voice Satellite
Open WebUI → LiteLLM Proxy → Ollama/vLLM
Paperless-NGX → NAS storage → LlamaIndex → Qdrant (RAG embeddings)
```

---

## 3. Inference Architecture

### 3.1 Model Lineup

| Model | Quant | VRAM | GPU | Role | Loaded |
|-------|-------|------|-----|------|--------|
| Qwen3-30B-A3B | Q5_K_M | ~18 GB | 3090 | Primary reasoning + planning | On-demand |
| Qwen3-8B | Q8_0 | ~9 GB | 1080 Ti | Fallback reasoning | On-demand |
| Qwen3-4B | Q8_0 | ~5 GB | 1080 Ti | Router / classifier | Always |
| nomic-embed-text-v1.5 | F16 | ~0.3 GB | 1080 Ti | Embeddings | Always |
| Home-LLM 3B | Q8_0 | ~3.5 GB | CPU | HA device control fallback | Always |
| faster-whisper large-v3-turbo | F16 | ~6 GB | 3090 | Speech-to-text | Always |

### 3.2 LiteLLM Proxy Configuration

LiteLLM on the Agent Node acts as the single inference endpoint for all consumers (Agent Orchestrator, Open WebUI, HA). It handles failover, retries, and model routing.

```yaml
# litellm_config.yaml
model_list:
  - model_name: "assistant"
    litellm_params:
      model: "ollama/qwen3:30b-a3b-q5_K_M"
      api_base: "http://inference:11434"
      timeout: 120
      stream: true
    model_info:
      priority: 1

  - model_name: "assistant"
    litellm_params:
      model: "ollama/qwen3:8b-q8_0"
      api_base: "http://localhost:11434"
      timeout: 60
      stream: true
    model_info:
      priority: 2

  - model_name: "assistant-fast"
    litellm_params:
      model: "ollama/qwen3:4b-q8_0"
      api_base: "http://localhost:11434"
      timeout: 30
      stream: true

  - model_name: "embeddings"
    litellm_params:
      model: "ollama/nomic-embed-text:v1.5"
      api_base: "http://localhost:11434"

router_settings:
  routing_strategy: "simple-shuffle"  # within same priority
  num_retries: 2
  timeout: 120
  retry_after: 5
  fallbacks: [{"assistant": ["assistant"]}]  # cascades through priorities
  allowed_fails: 3
  cooldown_time: 60
```

### 3.3 Failover Chain

```
Request → LiteLLM Proxy
  ├─ Priority 1: Qwen3-30B-A3B on 3090 (Ollama)
  │    └─ If timeout/error after 2 retries...
  ├─ Priority 2: Qwen3-8B on 1080 Ti (Ollama)
  │    └─ If timeout/error after 2 retries...
  └─ Priority 3: Home-LLM 3B on CPU (HA-only, device control)
       └─ If all fail → static error response
```

---

## 4. Voice Pipeline

### 4.1 Flow

```
[ESP32 Satellite]
    │ microWakeWord (on-device)
    ▼
[Wyoming Protocol → Gateway HA]
    │
    ▼
[Inference: openWakeWord] ← server-side confirmation (optional)
    │
    ▼
[Inference: faster-whisper] → text transcript
    │
    ▼
[Inference: SpeechBrain] → speaker_id → Authentik user mapping
    │
    ▼
[Agent: Orchestrator] → planning → tool calls → response text
    │
    ▼
[Inference: Piper TTS] → audio stream
    │
    ▼
[Wyoming Protocol → ESP32 Speaker]
```

### 4.2 Session Continuity

Voice conversations maintain a session thread for 5 minutes after the last interaction. Within this window, new wake-word activations resume the same LangGraph thread rather than starting fresh. The session maps: `(satellite_id, speaker_id) → thread_id`.

### 4.3 Output Channel Routing

The orchestrator decides where to deliver results based on content type:

| Content Type | Estimated Length | Primary Channel | Secondary Channel |
|-------------|-----------------|-----------------|-------------------|
| Confirmations | < 15 words | Voice | — |
| Short answers | 15-50 words | Voice | — |
| Medium content | 50-200 words | Voice (summary) | Push notification (full) |
| Long content | 200+ words | Voice (brief ack) | Open WebUI conversation |
| Technical/reference | Any | Voice (brief ack) | Open WebUI + push link |
| Async task results | Any | Push notification | Open WebUI conversation |

---

## 5. Memory Architecture

### 5.1 Dual-Mode Scoping

Every memory entry and document embedding carries metadata:

```json
{
  "scope": "personal",        // "family" | "personal"
  "user_id": "michael",       // Authentik user ID
  "memory_type": "fact",      // "fact" | "preference" | "event" | "pending" | "pattern"
  "timestamp": "2026-03-01T10:00:00Z",
  "tags": ["motorcycle", "suspension"],
  "source": "conversation",   // "conversation" | "document" | "automation"
  "doc_ref": null              // Paperless-NGX doc ID if linked
}
```

**Query filters by mode:**
- Family mode: `scope == "family"`
- Personal mode: `scope == "family" OR (scope == "personal" AND user_id == current_user)`

### 5.2 Memory States

| State | Description | Transitions |
|-------|-------------|-------------|
| `pending` | Recommended but unconfirmed (e.g., "try 12 clicks rebound") | → `confirmed` on user validation |
| `confirmed` | User-validated facts | → updated/invalidated by Mem0 |
| `pattern` | Detected repeated behavior (e.g., "movie night" scene) | → HA automation on user approval |

### 5.3 Memory Promotion

When the agent detects family-relevant information in a personal conversation (schedules, events, shared logistics), it auto-promotes to family scope. The promotion criteria:
- Calendar events involving family members
- Shared logistics (pickup times, appointments)
- Home maintenance schedules
- Meal plans

### 5.4 Qdrant Configuration

```yaml
# Collections
collections:
  - name: memories
    vectors:
      size: 768           # nomic-embed-text dimensions
      distance: Cosine
    on_disk_payload: true  # Prevent OOM during indexing
    
  - name: documents
    vectors:
      size: 768
      distance: Cosine
    on_disk_payload: true

  - name: conversations
    vectors:
      size: 768
      distance: Cosine
    on_disk_payload: true
```

---

## 6. Agent Orchestrator Architecture

### 6.1 Overview

The Agent Orchestrator is the **primary custom software component**. It is a Python application using LangGraph for workflow orchestration, with MCP clients for tool access, Mem0 for memory management, and LiteLLM as the inference backend.

### 6.2 Request Flow

```
Input (voice transcript / Open WebUI message / HA event)
    │
    ▼
[Session Manager] → resolve/create thread_id
    │
    ▼
[Meta-Reasoning Router] (Qwen3-4B on 1080 Ti)
    │  Classifies intent, selects tool subset (max 7), decides:
    │  - sync vs async execution
    │  - voice vs text output channel
    │  - parallel vs sequential tool plan
    ▼
[LangGraph Workflow Engine]
    │
    ├─ Simple command → HA intent / single tool call
    ├─ Multi-step task → Plan-then-execute with checkpointing
    ├─ Research task → Async queue via Celery
    └─ Interactive diagnostic → ReAct loop with HITL
    │
    ▼
[Response Synthesizer] → format for output channel
    │
    ▼
[Output Router] → voice / Open WebUI / push notification / HA notification
```

### 6.3 LangGraph State Schema

```python
from typing import TypedDict, Annotated, Literal
from langgraph.graph import add_messages

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    thread_id: str
    user_id: str
    scope: Literal["family", "personal"]
    speaker_id: str | None
    plan: list[dict] | None          # Decomposed task steps
    plan_step: int                    # Current step index
    tool_results: dict                # Accumulated tool outputs
    memory_context: list[dict]        # Retrieved memories
    output_channel: str               # "voice" | "webui" | "push" | "ha_notify"
    token_budget: int                 # Remaining tokens for this chain
    iteration_count: int              # Circuit breaker counter
    max_iterations: int               # Hard cap (default 15)
```

### 6.4 Safety Stack

| Layer | Mechanism | Config |
|-------|-----------|--------|
| Max iterations | `max_iterations=15` per chain | LangGraph `recursion_limit` |
| Time budget | 120s max execution time | Celery task timeout |
| Token budget | 50K tokens per chain | Tracked in state, triggers summarization at 80% |
| Loop detection | Same tool + similar args within 3-call window | Custom LangGraph node |
| Circuit breaker | 3 consecutive failures → trip | Per-tool failure counter |
| Output validation | Pydantic models for all tool call arguments | Per-step validation |
| Constrained decoding | GBNF grammar for tool call JSON | vLLM structured output |

### 6.5 Checkpointing

All LangGraph workflows use SQLite persistence:

```python
from langgraph.checkpoint.sqlite import SqliteSaver

checkpointer = SqliteSaver.from_conn_string("/data/langgraph/checkpoints.db")
graph = builder.compile(checkpointer=checkpointer)
```

This enables:
- Resume after crash (auto-recovery from last checkpoint)
- Multi-conversation workflows (same thread_id across sessions)
- Time travel debugging (replay from any checkpoint)
- HITL interrupts (pause indefinitely, resume when user responds)

---

## 7. MCP Server Registry

### 7.1 Deployed MCP Servers

| Server | Source | Location | Purpose |
|--------|--------|----------|---------|
| ha-mcp | homeassistant-ai | Agent Node | 80+ HA tools (lights, climate, media, etc.) |
| mcp-filesystem | modelcontextprotocol | Agent Node | NAS file access |
| mcp-memory | modelcontextprotocol | Agent Node | Knowledge graph (supplements Qdrant) |
| mcp-searxng | community | Agent Node | Web search via SearXNG |
| mcp-fetch | modelcontextprotocol | Agent Node | Web content retrieval |
| mcp-playwright | community | Agent Node | Browser automation |

### 7.2 Custom MCP Servers (to be built)

| Server | Purpose | Complexity |
|--------|---------|------------|
| mcp-notifications | Push results to HA, Open WebUI, ntfy | Medium |
| mcp-memory-scoped | Qdrant + Mem0 with dual-mode scoping | High |
| mcp-calendar | CalDAV integration (Nextcloud/Google) | Medium |
| mcp-shopping-list | HA shopping list + Todoist | Low |
| mcp-routines | CRUD for user-defined routines | Medium |
| mcp-workflow-status | Query/resume pending async workflows | Medium |

---

## 8. Resilience Architecture

### 8.1 Failure Modes and Responses

| Failure | Detection | Response | Recovery Time |
|---------|-----------|----------|---------------|
| 3090 Ollama down | LiteLLM health check (10s) | Failover to 1080 Ti (8B model) | < 30s |
| 1080 Ti Ollama down | LiteLLM health check | Failover to CPU (3B model) | < 30s |
| All inference down | LiteLLM circuit break | HA built-in intents only | Until restart |
| Qdrant down | Health check (10s) | Agent operates without memory (degraded) | < 60s (WAL replay) |
| NAS unreachable | NFS mount check (30s) | RAG unavailable; cached models still work | Until NAS recovers |
| Internet out | SearXNG engine timeout | Search disabled; all local services continue | Until restored |
| Redis down | Health check | Async tasks queue locally; sessions in-memory | < 10s (restart) |
| Power loss | UPS / NUT | Graceful shutdown at 5 min remaining | Full reboot (~3 min) |

### 8.2 Monitoring Stack

- **Uptime Kuma** on Gateway: HTTP/TCP health checks for all services, 60-second intervals
- **Prometheus + Grafana** on Agent Node: deep metrics from vLLM (native), Ollama (via ollama-metrics sidecar), Qdrant, Redis, node-exporter
- **nvidia_gpu_exporter**: GPU temp, utilization, VRAM on both GPU nodes
- **Alert routing**: Grafana Alertmanager → HA notification service → phone push via ntfy

### 8.3 Backup Strategy

| Data | Method | Frequency | Target | Retention |
|------|--------|-----------|--------|-----------|
| Qdrant snapshots | API snapshot + rsync | Daily | NAS | 30 days |
| LangGraph SQLite | `.backup` command | Hourly | NAS | 7 days |
| HA config | Git + rsync | Daily | NAS + offsite | 90 days |
| Paperless-NGX DB | pg_dump sidecar | Daily | NAS | 30 days |
| Docker volumes | Restic incremental | Daily | NAS | 30 days |
| Model weights | Manual copy | On change | NAS (archive) | Permanent |
| Full VM snapshots | Proxmox Backup Server | Weekly | NAS | 4 weeks |
| Offsite | Restic → Backblaze B2 | Weekly | B2 | 90 days |

---

## 9. Deployment Phases

### Phase 1: Voice Pipeline + Basic HA (Week 1-2)
- Deploy Ollama on Inference Engine with Qwen3-30B-A3B
- Deploy faster-whisper + Piper TTS on Inference Engine
- Configure HA Ollama conversation agent
- Set up ESP32 voice satellites with microWakeWord
- Deploy Caddy + Pi-hole on Gateway
- Test: voice commands control lights, thermostat

### Phase 2: Agent Orchestrator + Memory (Week 3-5)
- Deploy Qdrant + Mem0 on Agent Node
- Build Agent Orchestrator (LangGraph core)
- Deploy LiteLLM proxy
- Build mcp-memory-scoped (dual-mode memory)
- Build mcp-notifications
- Replace HA's built-in Ollama agent with orchestrator as conversation agent
- Deploy SpeechBrain for speaker identification
- Test: personalized responses, memory persistence, failover

### Phase 3: RAG + Research (Week 6-7)
- Deploy Paperless-NGX on NAS
- Build RAG pipeline (LlamaIndex + Qdrant)
- Deploy SearXNG + Perplexica on Gateway/Agent
- Deploy Celery worker for async tasks
- Test: document retrieval, web research, async delivery

### Phase 4: Full MCP + Polish (Week 8-10)
- Build remaining custom MCP servers
- Deploy Playwright for browser automation
- Build routine configuration system
- Deploy monitoring stack (Uptime Kuma, Prometheus, Grafana)
- Implement backup automation
- Load test concurrent users
- Test: complete scenarios from simulation document

---

## 10. Gap Registry (v1.2)

33 gaps identified through use-case simulation. See individual server specs for implementation details.

| # | Gap | Fix | Owner | Phase |
|---|-----|-----|-------|-------|
| 1 | No memory in HA voice path | Agent Orchestrator as HA conversation agent | Agent | 2 |
| 2 | Two separate brains | Unified agent with HA MCP + Memory MCP | Agent | 2 |
| 3 | Personal→Family memory promotion | Auto-promote family-relevant memories | Agent | 2 |
| 4 | No calendar integration | Calendar MCP server | Agent | 4 |
| 5 | No age-appropriate filtering | User profiles with role/age in Qdrant | Agent | 2 |
| 6 | No document access control in RAG | Scope metadata on document embeddings | Agent | 3 |
| 7 | No planning/routing step | Meta-reasoning router (4B model) | Agent | 2 |
| 8 | No async tasks | Celery + Redis worker queue | Agent+GW | 3 |
| 9 | No result delivery | mcp-notifications server | Agent | 2 |
| 10 | Research doesn't use memory | Planning enriches queries with memory | Agent | 3 |
| 11 | LLM can't handle concurrent requests | vLLM for batched inference | Inference | 2 |
| 12 | STT bottleneck on concurrent audio | Second whisper on 1080 Ti for overflow | Agent | 4 |
| 13 | No shopping/task list | mcp-shopping-list | Agent | 4 |
| 14 | No location context | Home coords in user profile | Agent | 2 |
| 15 | Sequential parallelizable steps | LangGraph Send API fan-out | Agent | 2 |
| 16 | No state persistence | LangGraph SQLite checkpointer | Agent | 2 |
| 17 | Too many tools | Tool routing layer (max 7 per request) | Agent | 2 |
| 18 | Parallel HA calls serialize | vLLM Hermes parser for parallel calls | Inference | 2 |
| 19 | No scene/routine learning | Memory → automation promotion | Agent | 4 |
| 20 | No HA action result validation | Per-action status check | Agent | 2 |
| 21 | Memory-to-document linking | Store Paperless doc IDs in memory | Agent | 3 |
| 22 | Voice wrong for long content | Output channel routing | Agent | 2 |
| 23 | RAG chunks accumulate | Context budget with summarization | Agent | 3 |
| 24 | Multi-conversation workflow threading | LangGraph thread persistence + dashboard | Agent | 3 |
| 25 | No commerce integration | Bookmark-and-track with reminders | Agent | 4 |
| 26 | HITL resume from any interface | Workflow resume API endpoint | Agent | 3 |
| 27 | Parallel results blow up context | Per-source 300-token cap | Agent | 2 |
| 28 | No content filtering | Audience-aware filtering (speaker detection) | Agent | 4 |
| 29 | Routines hardcoded | mcp-routines structured definitions | Agent | 4 |
| 30 | MCP cold-start latency | Always-on for daily-use MCP servers | All | 1 |
| 31 | No voice session continuity | 5-min session thread persistence | Agent | 2 |
| 32 | Synchronous ReAct slow for voice | Speculative pre-computation | Agent | 4 |
| 33 | Memory writes ambiguous | Pending/confirmed states | Agent | 2 |
| 34 | No inference failover | LiteLLM proxy with priority routing | Agent | 1 |
| 35 | HA silent when LLM down | Health-check automation + fallback pipeline | Gateway | 1 |
| 36 | NAS stale mounts freeze containers | soft,intr NFS + local model storage | All | 1 |
| 37 | No monitoring | Uptime Kuma + Prometheus + Grafana | GW+Agent | 1 |
| 38 | No backups | Restic + Qdrant snapshots + pg_dump | All | 1 |
| 39 | OOM/memory leaks | OLLAMA_MAX_LOADED_MODELS=1 + memory limits | Inference | 1 |
| 40 | Proxmox quorum with 4 nodes | QDevice on Raspberry Pi / Gateway | Gateway | 1 |
| 41 | GPU passthrough recovery | NVIDIA Container Toolkit over VFIO | Inference | 1 |
| 42 | Power loss corruption | UPS + NUT + ZFS | All | 1 |
