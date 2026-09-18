# Offline-First Home AI Agent

A voice-first, offline-capable AI agent distributed across a 4-node Proxmox homelab cluster, with a Home Assistant VM as the voice/conversation front-end.

**Current status:** Phase 2 is deployed and healthy end-to-end — agent-orchestrator,
memory, calendar, notifications, and the HA voice bridge all work. See
`ARCHITECTURE.md` for the full current-state breakdown and `QUESTIONS.md` for
what's still open.

## Quick Start

```bash
# 1. Copy and fill in environment variables
cp .env.template .env
nano .env

# 2. Deploy all nodes in dependency order (each is a standalone rsync
#    target, not a git checkout — see ARCHITECTURE.md's "Redeploying /
#    Making Changes" for the per-service commands this project actually
#    uses day to day, which differ from this script for agent-orchestrator
#    specifically)
./scripts/deploy.sh deploy
# or a single node:
./scripts/deploy.sh deploy <gateway|agent|inference>

# 3. Pull models onto GPU nodes
./scripts/deploy.sh pull-models

# 4. Check system health
./scripts/deploy.sh status
```

## Repository Structure

```
├── ARCHITECTURE.md                   # Current node/service status — start here
├── SYSTEM_SPEC.md                    # Master system specification
├── QUESTIONS.md                      # Open decisions (gitignored, local only)
├── ARCHITECTURE_DIAGRAM.svg          # Visual architecture diagram
├── PROJECT_KNOWLEDGE.md              # Full context for Claude Project
├── .env.template                     # Environment variables template
│
├── servers/
│   ├── gateway/                      # Mini PC — Caddy, Pi-hole, Authentik, monitoring
│   ├── agent/                        # 1080 Ti — orchestrator, vector DB, chat UI, LiteLLM
│   │   └── config/                   # litellm_config.yaml, prometheus.yml, grafana/
│   ├── inference/                    # RTX 3090 — primary LLM, STT/TTS/wake word
│   └── nas/                          # Synology — storage only (NFS)
│
├── custom-software/
│   ├── agent-orchestrator/           # LangGraph + FastAPI agent core
│   │   └── config/mcp_servers.json   # MCP server registry (source of truth;
│   │                                 #   the agent node also has a separately
│   │                                 #   bind-mounted runtime copy, see
│   │                                 #   ARCHITECTURE.md)
│   ├── mcp-memory-scoped/            # Qdrant-backed scoped memory
│   ├── mcp-notifications/            # HA-native multi-channel notifications
│   ├── mcp-calendar/                 # HA Google Calendar (extensible provider pattern)
│   ├── mcp-workflow-status/          # Async task status/resume/cancel
│   ├── speechbrain-speaker-id/       # Voice speaker ID — written, not deployed
│   ├── e2e/                          # End-to-end test suite against a live deploy
│   └── mcp-servers/SPEC.md           # MCP server config spec
│
├── ha_custom_component/              # HA integration bridging Assist → agent-orchestrator
│   └── custom_components/ha_jarvis/
│
└── scripts/
    ├── deploy.sh                     # Master deployment script
    ├── setup-nfs-mounts.sh           # NAS NFS mount setup (Phase 3 prerequisite)
    └── backup-cron.sh                # Automated backup (cron)
```

## Architecture Overview

```
Voice Satellite (ESPHome) ──▶ HA VM (192.168.13.20)
                                  │  wake word / STT / TTS: Wyoming → Inference node
                                  │  conversation agent: ha_jarvis (custom component)
                                  ▼
                          POST /ha/conversation/process
                                  │
                          agent-orchestrator (Agent node, :8100)
                                  │
                          ┌───────┼────────────────────┐
                          ▼       ▼                     ▼
                     LiteLLM   Qdrant memory      MCP servers (stdio/SSE):
                          │                       ha-mcp, mcp-calendar,
                          ▼                       mcp-notifications,
                 Ollama qwen3.8:27b               mcp-workflow-status,
                 (Inference node, RTX 3090)        mcp-memory-scoped,
                                                    google-workspace
                                  │
                          Response → voice (TTS) / push / Open WebUI
```

The agent node's own 1080 Ti (`ollama-agent`) only serves the local
`nomic-embed-text` embeddings model now — there's no local-LLM fallback tier;
`assistant` (qwen3.8:27b) is the only chat model, routed to the inference node.

## Deployment Phases

| Phase | Focus | Status |
|-------|-------|--------|
| 1 | Voice pipeline, basic HA, DNS, monitoring, resilience infra | Done |
| 2 | Agent Orchestrator, memory, LiteLLM, calendar, HA voice bridge | Done |
| 3 | RAG pipeline (Paperless), web research, async tasks | Blocked on NAS NFS setup |
| 4 | Shopping list / routines MCP servers, browser automation, polish | Not started |

Speaker ID (SpeechBrain) was originally scoped as part of Phase 2 but is
deploy-ready rather than done — see `QUESTIONS.md`.

## Custom Software

| Package | What it does | Status |
|---------|-------------|--------|
| `agent-orchestrator` | LangGraph + FastAPI + Celery agent core | Deployed, healthy |
| `mcp-memory-scoped` | Qdrant-backed scoped memory (personal/family) | Deployed |
| `mcp-notifications` | HA-native multi-channel output routing | Deployed |
| `mcp-calendar` | HA Google Calendar, extensible provider pattern | Deployed |
| `mcp-workflow-status` | Async workflow status/resume/cancel | Deployed |
| `google-workspace` | Gmail/Docs/Drive (PyPI `workspace-mcp`, not a package in this repo) | Deployed, needs OAuth setup to actually use |
| `speechbrain-speaker-id` | Per-speaker voice ID via ECAPA-TDNN + Qdrant | Written, not deployed |
| `mcp-shopping-list` | HA shopping list wrapper | Phase 4, not started |
| `mcp-routines` | User-defined automation routines | Phase 4, not started |
| `ha_custom_component` (`ha_jarvis`) | HA ↔ agent-orchestrator conversation bridge | Deployed on the HA VM, set as default pipeline agent |
