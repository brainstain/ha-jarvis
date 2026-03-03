# Project Knowledge: Offline-First Home AI Agent

> **Purpose:** This document is the knowledge base for a Claude Project. It contains the complete architectural context, design decisions, and implementation state for building an offline-first AI agent system distributed across a 4-node Proxmox homelab cluster. Use this to maintain continuity across conversations.

---

## System Identity

**What:** A voice-first, offline-capable AI home agent ("Jarvis") that manages home automation, maintains dual-mode memory, performs document retrieval, web research, and is extensible via MCP protocol.

**Who:** Michael — software engineering manager, Python developer, familiar with APIs, Docker, GitHub workflows. Also: motocross rider (KTM 300 XC), parent with young child, tracks macros, home construction projects.

**Where:** 4-node Proxmox cluster at home. Privacy-first, local LLM, cloud only when explicitly needed for web search.

---

## Hardware

| Node | Role | CPU | RAM | GPU | Storage |
|------|------|-----|-----|-----|---------|
| Gateway (Mini PC) | Network, DNS, HA, auth, monitoring | 4C | 16 GB | — | 512 GB SSD |
| Agent (Mid Server) | Orchestrator, vector DB, chat UI, secondary inference | 8C | 64 GB | GTX 1080 Ti (11 GB) | 3 TB mixed |
| Inference (Power Server) | Primary LLM, STT, TTS, speaker ID | 16C | 64 GB | RTX 3090 (24 GB) | 2 TB RAID 1 NVMe |
| NAS (Synology) | Documents, backups, media | — | — | — | 16 TB |

---

## Critical Design Decisions

### 1. Model Selection: Qwen3-30B-A3B (MoE) on vLLM/Ollama
- MoE activates only ~3B parameters per forward pass → fast inference on 3090
- **CRITICAL FINDING:** MoE models underperform dense models on multi-step tool calling. Benchmark shows 79% on 5-10 step chains vs dense 8B at 92%. MoE weak at final-step actions.
- Mitigation: plan-then-execute pattern, constrained decoding, per-step validation, Q5_K_M minimum quantization (Q4 causes repetition loops)
- Consider running dense Qwen3-8B alongside for tool execution, MoE for planning

### 2. LiteLLM Proxy for Inference Routing
- Single endpoint for all consumers (orchestrator, Open WebUI, HA)
- Priority failover: 30B on 3090 → 8B on 1080 Ti → 3B on CPU → HA built-in intents
- 2 retries per backend, 60s cooldown on failures

### 3. LangGraph for Orchestration (Not Pure ReAct)
- Plan-then-execute beats pure ReAct for local models
- SQLite checkpointing for crash recovery and multi-conversation workflows
- Send API for parallel fan-out (parallelizable steps like weather + calendar + memory)
- HITL interrupts for async workflows that span hours/days

### 4. MCP Protocol for All Integrations
- New capabilities = new MCP server, no agent code changes
- Tool routing layer: meta-reasoning router (4B model) selects max 7 tools per request
- Custom MCP servers: memory-scoped, notifications, calendar, shopping-list, routines, workflow-status
- Third-party: ha-mcp, filesystem, fetch, playwright

### 5. Dual-Mode Memory (Family/Personal)
- Qdrant vector DB with Mem0 management layer
- Every memory has scope (family/personal), user_id, memory_type
- Three memory states: pending → confirmed, pattern → HA scene
- Family mode sees only family scope; Personal mode sees family + own personal
- Speaker identification (SpeechBrain) determines user and scope

### 6. Voice Pipeline
- ESP32 satellites → microWakeWord (on-device) → Wyoming protocol → HA
- STT: faster-whisper large-v3-turbo on 3090 (~3 GB VRAM)
- TTS: Piper (CPU) on Inference node
- 5-minute voice session continuity (same thread across wake-word activations)
- Output channel routing: voice for short, push for medium, Open WebUI for long/technical

---

## Architecture v1.2 Gap Registry (42 Gaps)

Gaps identified through 12 use-case simulations including multi-step execution and failure scenarios. Organized by deployment phase.

### Phase 1 (Infrastructure)
| # | Gap | Fix |
|---|-----|-----|
| 30 | MCP cold-start latency | Always-on for daily-use servers |
| 34 | No inference failover | LiteLLM proxy with priority routing |
| 35 | HA silent when LLM down | Health-check automation + fallback pipeline |
| 36 | NAS stale mounts freeze containers | soft,intr NFS + local model storage |
| 37 | No monitoring | Uptime Kuma + Prometheus + Grafana |
| 38 | No backups | Restic + Qdrant snapshots + pg_dump |
| 39 | OOM/memory leaks | OLLAMA_MAX_LOADED_MODELS=1 + memory limits + daily restart |
| 40 | Proxmox quorum with 4 nodes | QDevice on Raspberry Pi / Gateway |
| 41 | GPU passthrough recovery | NVIDIA Container Toolkit over VFIO |
| 42 | Power loss corruption | UPS + NUT + ZFS |

### Phase 2 (Core Agent)
| # | Gap | Fix |
|---|-----|-----|
| 1 | No memory in HA voice path | Agent Orchestrator as HA conversation agent |
| 2 | Two separate brains | Unified agent with HA MCP + Memory MCP |
| 3 | Personal→Family memory promotion | Auto-promote family-relevant memories |
| 5 | No age-appropriate filtering | User profiles with role/age in Qdrant |
| 7 | No planning/routing step | Meta-reasoning router (4B model) |
| 9 | No result delivery | mcp-notifications server |
| 14 | No location context | Home coords in user profile |
| 15 | Sequential parallelizable steps | LangGraph Send API fan-out |
| 16 | No state persistence | LangGraph SQLite checkpointer |
| 17 | Too many tools | Tool routing layer (max 7 per request) |
| 18 | Parallel HA calls serialize | vLLM Hermes parser |
| 20 | No HA action result validation | Per-action status check |
| 22 | Voice wrong for long content | Output channel routing |
| 27 | Parallel results blow up context | Per-source 300-token cap |
| 31 | No voice session continuity | 5-min session thread persistence |
| 33 | Memory writes ambiguous | Pending/confirmed states |

### Phase 3 (RAG + Research + Async)
| # | Gap | Fix |
|---|-----|-----|
| 6 | No document access control | Scope metadata on embeddings |
| 8 | No async tasks | Celery + Redis worker queue |
| 10 | Research doesn't use memory | Planning enriches queries |
| 21 | Memory-to-document linking | Store Paperless doc IDs in memory |
| 23 | RAG chunks accumulate | Context budget with summarization |
| 24 | Multi-conversation threading | LangGraph thread persistence + dashboard |
| 26 | HITL resume from any interface | Workflow resume API endpoint |

### Phase 4 (Full MCP + Polish)
| # | Gap | Fix |
|---|-----|-----|
| 4 | No calendar | Calendar MCP server |
| 11 | Concurrent inference | vLLM batched inference |
| 12 | STT bottleneck | Second whisper on 1080 Ti |
| 13 | No shopping/task list | mcp-shopping-list |
| 19 | No scene/routine learning | Memory → automation promotion |
| 25 | No commerce integration | Bookmark-and-track with reminders |
| 28 | No content filtering | Audience-aware (speaker detection) |
| 29 | Routines hardcoded | mcp-routines structured definitions |
| 32 | Synchronous ReAct slow | Speculative pre-computation |

---

## Multi-Step Agent Safety Stack

| Layer | Mechanism | Config |
|-------|-----------|--------|
| Max iterations | `recursion_limit` | 15 per chain |
| Time budget | Celery timeout | 120s sync, 600s async |
| Token budget | Tracked in state | 50K per chain, summarize at 80% |
| Loop detection | Same tool + similar args × 3 | Custom LangGraph node |
| Circuit breaker | 3 consecutive failures | Per-tool counter |
| Output validation | Pydantic models | Per-step |
| Constrained decoding | GBNF grammar | vLLM structured output |

**Compounding error math:** 5-step chain at 95% per-step = 77.4% success. At 99% per-step (with validation + constrained decoding) = 95.1%.

---

## Resilience Patterns

**OOM is the #1 failure mode.** Ollama pre-v0.7.0 has documented VRAM leak. Mitigations:
- `OLLAMA_MAX_LOADED_MODELS=1` on 3090
- `OLLAMA_KEEP_ALIVE=5m` or `-1` (depending on usage pattern)
- Docker `--memory=16g` limits
- Daily cron restart of Ollama

**NAS failures create stale mounts.** Use `soft,intr,timeo=10` for NFS. Keep model weights, Qdrant data, and database files on LOCAL NVMe only.

**Proxmox 4-node cluster needs QDevice** for quorum (otherwise 2-node failure = total lockout).

**UPS + NUT is not optional.** Power loss causes GPU passthrough breakage, filesystem corruption, potential Proxmox reinstall.

---

## Context Engineering for Local Models

- **Observation masking** (JetBrains): Replace old tool outputs with placeholders, keep recent. Cheaper than LLM summarization, equal or better performance.
- **Per-source token caps**: Limit each tool result to ~300 tokens in parallel fan-out.
- **Letta/MemGPT tiered memory**: Main context = "RAM", archival memory (Qdrant) = "disk". Agent manages own memory via tool calls.
- **Sleep-time compute**: Background agents run during idle periods, refining memories. Up to 13% improvement.

---

## Deliverables Created

### Spec Documents
- `SYSTEM_SPEC.md` — Master system specification
- `servers/gateway/SERVER_SPEC.md` — Gateway node spec
- `servers/agent/SERVER_SPEC.md` — Agent node spec
- `servers/inference/SERVER_SPEC.md` — Inference node spec
- `servers/nas/SERVER_SPEC.md` — NAS node spec
- `custom-software/agent-orchestrator/SPEC.md` — Agent Orchestrator software spec
- `custom-software/mcp-servers/SPEC.md` — Custom MCP servers spec

### Docker Compose Files
- `servers/gateway/docker-compose.yml`
- `servers/agent/docker-compose.yml`
- `servers/inference/docker-compose.yml`
- `servers/nas/docker-compose.yml`

### Configuration
- `config/prometheus.yml` — Prometheus scrape config
- `config/litellm_config.yaml` — LiteLLM routing (referenced in system spec)
- `servers/gateway/traefik/dynamic.yml` — Traefik route reference config (for external Traefik host)
- `.env.template` — Environment variables template

### Scripts
- `scripts/deploy.sh` — Master deployment (deploy all, per-node, status, pull-models, backup, logs)
- `scripts/backup-cron.sh` — Automated backup for cron scheduling

### Diagrams
- `ARCHITECTURE_DIAGRAM.svg` — Full system architecture visual

---

## Custom Software To Build

### 1. Agent Orchestrator (Phase 2 — HIGH priority)
- Python 3.12, FastAPI + LangGraph + Celery
- The central brain: receives all inputs, routes through planning layer, executes workflows
- API: `/chat`, `/chat/async`, `/tasks/{id}`, `/tasks/{id}/resume`, `/ws/chat`
- LangGraph state: messages, thread_id, user_id, scope, plan, tool_results, token_budget
- See `custom-software/agent-orchestrator/SPEC.md` for full API spec and data models

### 2. mcp-memory-scoped (Phase 2 — HIGH priority)
- Dual-mode Qdrant + Mem0 memory with family/personal scoping
- Tools: memory_search, memory_store, memory_confirm, memory_promote, memory_update, memory_delete

### 3. mcp-notifications (Phase 2 — MEDIUM priority)
- Multi-channel output: HA notify, ntfy push, Open WebUI post, TTS speak
- Tools: notify_ha, notify_push, notify_webui, notify_tts

### 4. mcp-calendar (Phase 4)
- CalDAV integration
- Tools: calendar_list_events, calendar_create_event, calendar_find_free_time

### 5. mcp-shopping-list (Phase 4)
- Wraps HA shopping list API
- Tools: shopping_list_add, shopping_list_view, shopping_list_complete

### 6. mcp-routines (Phase 4)
- CRUD for user-defined automation routines (morning briefing, movie night)
- Routines stored in Qdrant with structured JSON payloads

### 7. mcp-workflow-status (Phase 3)
- Query/manage async workflows and HITL interrupts
- Reads LangGraph SQLite checkpoints + Celery result backend

---

## Key Research Findings to Remember

1. **smolagents CodeAgent** reduces steps by 30% vs JSON tool calling — code naturally handles loops/conditionals. Worth embedding inside LangGraph nodes.
2. **Qwen-Agent framework** handles Qwen-specific tool-calling templates natively. When serving Qwen3 via vLLM, do NOT use `--enable-auto-tool-choice --tool-call-parser hermes` — Qwen-Agent parses natively.
3. **MCP has no built-in chaining.** Workarounds: mcp-tool-chainer with JsonPath, programmatic tool calling via code execution (60-98% token reduction), MCP Tasks primitive for long-running ops.
4. **XGrammar** achieves up to 100× speedup for constrained decoding by splitting tokens into context-independent (~99%) and context-dependent (~1%) categories.
5. **Two-phase approach**: unconstrained thinking + constrained generation preserves reasoning while guaranteeing structural correctness.
6. **Anthropic orchestrator-worker pattern** outperformed single-agent by 90.2% (at 15× token cost). For home agent: simple tasks = 1 agent, comparisons = 2-4 subagents, complex research = fan-out.
7. **Home Assistant `ha-fallback-conversation`** was deprecated. Use health-check automation that polls Ollama and switches pipeline assignments on failure.
8. **Qdrant requires block-level storage.** NFS does not work. Must be local NVMe.
