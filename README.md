# Offline-First Home AI Agent

A voice-first, offline-capable AI agent distributed across a 4-node Proxmox homelab cluster.

## Quick Start

```bash
# 1. Copy and fill in environment variables
cp .env.template .env
nano .env

# 2. Deploy all nodes in dependency order
./scripts/deploy.sh deploy

# 3. Pull models onto GPU nodes
./scripts/deploy.sh pull-models

# 4. Check system health
./scripts/deploy.sh status
```

## Repository Structure

```
├── SYSTEM_SPEC.md                    # Master system specification
├── ARCHITECTURE_DIAGRAM.svg          # Visual architecture diagram
├── PROJECT_KNOWLEDGE.md              # Full context for Claude Project
├── .env.template                     # Environment variables template
│
├── servers/
│   ├── gateway/                      # Mini PC — DNS, HA, auth, monitoring
│   │   ├── SERVER_SPEC.md
│   │   ├── docker-compose.yml
│   │   └── traefik/dynamic.yml       # Reference config for external Traefik
│   ├── agent/                        # Mid Server — orchestrator, vector DB, UI
│   │   ├── SERVER_SPEC.md
│   │   └── docker-compose.yml
│   ├── inference/                    # Power Server — LLM, STT, TTS
│   │   ├── SERVER_SPEC.md
│   │   └── docker-compose.yml
│   └── nas/                          # Synology — storage only (NFS/SMB)
│       └── SERVER_SPEC.md
│
├── custom-software/
│   ├── agent-orchestrator/           # LangGraph + FastAPI agent core
│   │   ├── SPEC.md
│   │   ├── Dockerfile
│   │   └── pyproject.toml
│   └── mcp-servers/                  # Custom MCP server specifications
│       └── SPEC.md
│
├── config/
│   └── prometheus.yml                # Prometheus scrape configuration
│
└── scripts/
    ├── deploy.sh                     # Master deployment script
    └── backup-cron.sh                # Automated backup (cron)
```

## Architecture Overview

```
Voice Satellite → HA (Gateway) → Agent Orchestrator (Agent Node)
                                      ↓
                              LiteLLM Proxy → Ollama 3090 (Inference)
                                      ↓              ↓ failover
                              MCP Servers        Ollama 1080 Ti (Agent)
                                      ↓
                              Qdrant Memory + Paperless RAG (NAS)
                                      ↓
                              Response → Voice / Push / WebUI
```

## Deployment Phases

| Phase | Weeks | Focus |
|-------|-------|-------|
| 1 | 1-2 | Voice pipeline, basic HA, DNS, monitoring, resilience infra |
| 2 | 3-5 | Agent Orchestrator, memory, LiteLLM, speaker ID |
| 3 | 6-7 | RAG pipeline, web research, async tasks |
| 4 | 8-10 | Full MCP suite, routines, browser automation, polish |

## Custom Software To Build

1. **Agent Orchestrator** — LangGraph + FastAPI + Celery (Phase 2)
2. **mcp-memory-scoped** — Dual-mode Qdrant+Mem0 memory (Phase 2)
3. **mcp-notifications** — Multi-channel output routing (Phase 2)
4. **mcp-calendar** — CalDAV integration (Phase 4)
5. **mcp-shopping-list** — HA shopping list wrapper (Phase 4)
6. **mcp-routines** — User-defined automation routines (Phase 4)
7. **mcp-workflow-status** — Async workflow management (Phase 3)
