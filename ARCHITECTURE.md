# Homelab AI — Architecture & Current State

## Physical Nodes

| Node | IP | Hardware | Role |
|------|----|----------|------|
| Gateway | 192.168.13.29 | Mini PC | Reverse proxy, DNS, auth, monitoring |
| Agent | 192.168.13.22 | GTX 1080 Ti | Orchestration, LLM proxy, vector DB, chat UI |
| Inference | 192.168.13.15 | RTX 3090 | Primary LLM, voice pipeline (STT/TTS/wake word) |
| NAS | 192.168.13.12 | Synology | Storage only — NFS exports for agent node |

Pi-hole on the gateway serves as LAN DNS. All services are accessible via
`*.michaelgoldstein.co` with automatic TLS through Caddy + Route53 DNS-01
(no port forwarding required).

---

## Node Status

### Gateway — Fully Deployed ✅

All 9 containers healthy.

| Service | URL | Notes |
|---------|-----|-------|
| Caddy | — | TLS termination for all services |
| Pi-hole | pihole.michaelgoldstein.co | LAN DNS, 24 local records |
| Authentik | authentik.michaelgoldstein.co | SSO (not yet wired into services) |
| SearXNG | search.michaelgoldstein.co | Self-hosted metasearch |
| Uptime Kuma | uptime.michaelgoldstein.co | Service monitoring |
| Redis | — | Broker for agent Celery tasks |
| NUT | — | UPS monitoring |

---

### Inference Node — Fully Deployed ✅

| Service | Port | Status |
|---------|------|--------|
| Ollama (qwen3:30b) | 11434 | Healthy — 18 GB model on RTX 3090 |
| Wyoming Whisper | 10300 | Healthy — STT for HA voice pipeline |
| Piper TTS | 10200 | Healthy — TTS for HA voice pipeline |
| OpenWakeWord | 10400 | Healthy — wake word for HA |
| Metrics exporters | 9100/9835/9091 | Healthy — scraped by agent Prometheus |

The voice pipeline services are deployed but **not yet wired into Home Assistant**.
HA needs to point its Wyoming integration at `inference.michaelgoldstein.co` on
ports 10300 (STT), 10200 (TTS), and 10400 (wake word).

---

### Agent Node — Phase 2 Deployed ✅

| Service | Status | Notes |
|---------|--------|-------|
| Ollama-agent | Up (healthcheck cosmetic) | Models loaded: qwen3:4b, qwen3:8b, nomic-embed-text:v1.5 |
| LiteLLM | Up — inference working ✅ | All 4 models responding; Docker healthcheck shows (unhealthy) but is a false alarm — see note |
| Prometheus | Healthy | Scrapes agent + inference node |
| Grafana | Up | grafana.michaelgoldstein.co |
| **Qdrant** | **Healthy** | Vector DB for agent memory — port 6333 |
| **agent-orchestrator** | **Healthy** | Port 8100; `/health` returns `degraded` (ha-mcp unreachable — expected, needs HA component) |
| **Open WebUI** | **Healthy** | Port 3000 — chat UI wired to LiteLLM |

**LiteLLM healthcheck note:** Docker reports `(unhealthy)` because LiteLLM's
built-in `/health` probe runs a live inference call to warm-check each model.
The local models (qwen3:4b, qwen3:8b) exceed the probe timeout on cold start,
and nomic-embed-text rejects a generate call (embeddings-only model). All three
respond correctly to actual requests. Verified: chat completions and 768-dim
embeddings both work.

---

### NAS (192.168.13.12) — Pending Manual Setup

Synology DiskStation. No containers run here. NFS exports need to be configured
in DSM for the agent node to mount before Phase 3 (Paperless) can start.

| Share | Synology path | Access |
|-------|--------------|--------|
| paperless/media | /volume1/paperless/media | ro |
| paperless/consume | /volume1/paperless/consume | rw |
| paperless/export | /volume1/paperless/export | ro |
| backups | /volume1/backups | rw |
| models | /volume1/models | ro |

After configuring DSM, run on the agent node:
```
bash /opt/homelab-ai/agent/scripts/setup-nfs-mounts.sh
```

---

## Custom Software (Written, Not Yet Deployed)

All source lives in `custom-software/`. The agent-orchestrator Docker image needs
to be built on the agent node. Nothing in this stack is running yet.

| Package | What it does |
|---------|-------------|
| `agent-orchestrator` | FastAPI + LangGraph agent. Handles chat, memory, tool dispatch, HA conversation protocol, WebSocket streaming. Three modes: simple (single-pass), multistep (planned steps), interactive (HITL with human confirmation). |
| `mcp-memory-scoped` | 7-tool MCP server backed by Qdrant. Scoped memory (personal vs. family) with auto-promotion for important facts. |
| `mcp-notifications` | 4-tool MCP server. HA-native push to mobile, persistent notifications, TTS on any media player, Open WebUI message injection. Discovers targets dynamically from HA REST API — no hardcoding. |
| `mcp-workflow-status` | 5-tool MCP server. Check, resume, and cancel async research tasks via the orchestrator REST API. |
| `mcp-calendar` | 4-tool MCP server. Read/write HA Google Calendar. Extensible provider pattern — add CalDAV etc. by dropping a file in `providers/`. |
| `speechbrain-speaker-id` | HTTP service for per-speaker voice identification. Enrollment API at `POST /enroll`. Not yet wired into HA. |

**MCP servers enabled when agent-orchestrator starts:** ha-mcp (HA tools),
mcp-memory-scoped, mcp-notifications, mcp-workflow-status. Calendar and others
are disabled until Phase 2 is running.

---

## How to Start Testing

### Step 1 — Deploy Phase 2 (agent + vector DB + chat UI)

```bash
# Sync custom software and updated configs to the agent node
rsync -avz custom-software/ michael@192.168.13.22:/opt/homelab-ai/custom-software/
rsync -av servers/agent/ michael@192.168.13.22:/opt/homelab-ai/agent/

# Build the agent-orchestrator image on the node
ssh michael@192.168.13.22 \
  "cd /opt/homelab-ai/custom-software/agent-orchestrator && \
   docker build -t agent-orchestrator:latest ."

# Start Phase 2 services
ssh michael@192.168.13.22 \
  "cd /opt/homelab-ai/agent && docker compose --profile phase2 up -d"
```

This starts: **Qdrant** (vector DB on port 6333), **agent-orchestrator** (port 8100),
**Open WebUI** (port 3000).

---

### Step 2 — Enable the Agent API in Caddy + DNS

In `servers/gateway/caddy/Caddyfile`, uncomment:
```
agent-api.michaelgoldstein.co {
    reverse_proxy 192.168.13.22:8100
}
```

In `servers/gateway/docker-compose.yml`, add to `FTLCONF_dns_hosts`:
```
192.168.13.29 agent-api.michaelgoldstein.co
```

Then sync and redeploy on the gateway:
```bash
rsync -av servers/gateway/ michael@192.168.13.29:/opt/homelab-ai/gateway/
ssh michael@192.168.13.29 "cd /opt/homelab-ai/gateway && \
  docker compose up -d --no-deps --force-recreate caddy pihole"
```

---

### Step 3 — Smoke Test the Stack

```bash
# Health check — shows MCP server connection status
curl http://192.168.13.22:8100/health

# Basic chat
curl -X POST http://192.168.13.22:8100/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What can you help me with?", "scope": "family"}'

# Open WebUI — open in browser
open https://webui.michaelgoldstein.co
```

---

### Step 4 — Wire the HA Voice Pipeline

In Home Assistant: **Settings → Voice Assistants → Add pipeline**

| Field | Value |
|-------|-------|
| Wake word | Wyoming — `inference.michaelgoldstein.co:10400` |
| Speech-to-text | Wyoming — `inference.michaelgoldstein.co:10300` |
| Text-to-speech | Wyoming — `inference.michaelgoldstein.co:10200` |
| Conversation agent | Home Assistant (for now — see note below) |

> **Note:** The full agent conversation integration (routing voice commands to
> `agent-orchestrator`) requires a custom HA component that bridges HA's
> conversation protocol to `POST /ha/conversation/process` on the agent.
> The orchestrator endpoint is already built and ready — the HA component
> is the remaining piece.

---

## What's Left After Phase 2

| Item | What it unlocks |
|------|----------------|
| Configure NFS on Synology DSM | Phase 3: Paperless document management |
| Phase 3 deploy | `docker compose --profile phase3 up -d` on agent node |
| HA custom conversation component | Full voice → agent pipeline |
| SpeechBrain speaker enrollment | Per-user memory scoping via voice |
| Get Open WebUI API key from admin panel | `notify_webui` tool (async task results in chat) |
| Authentik integration | SSO in front of exposed services |
