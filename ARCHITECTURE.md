# Homelab AI — Architecture & Current State

## Physical Nodes

| Node | IP | Hardware | Role |
|------|----|----------|------|
| Gateway | 192.168.13.29 | Mini PC | Reverse proxy, DNS, auth, monitoring |
| Agent | 192.168.13.22 | GTX 1080 Ti | Orchestration, LLM proxy, vector DB, chat UI |
| Inference | 192.168.13.15 | RTX 3090 | Primary LLM, voice pipeline (STT/TTS/wake word) |
| NAS | 192.168.13.12 | Synology | Storage only — NFS exports for agent node |
| Home Assistant | 192.168.13.20 | Separate VM | Voice pipeline + conversation agent front-end, out of deploy scope |

Pi-hole on the gateway serves as LAN DNS. All services are accessible via
`*.michaelgoldstein.co` with automatic TLS through Caddy + Route53 DNS-01
(no port forwarding required).

---

## Node Status

### Gateway — Fully Deployed ✅

All containers healthy.

| Service | URL | Notes |
|---------|-----|-------|
| Caddy | — | TLS termination for all services |
| Pi-hole | pihole.michaelgoldstein.co | LAN DNS |
| Authentik | authentik.michaelgoldstein.co | Running and healthy, but **not gating any other service yet** — no `forward_auth` wired into any other Caddyfile block. Deploying it isn't the remaining step; deciding which services to put behind it and configuring that in Authentik itself is. |
| SearXNG | search.michaelgoldstein.co | Self-hosted metasearch |
| Uptime Kuma | uptime.michaelgoldstein.co | Service monitoring |
| Redis | — | Broker for agent Celery tasks |
| NUT | — | UPS monitoring |

---

### Inference Node — Fully Deployed ✅, voice pipeline wired into HA

| Service | Port | Status |
|---------|------|--------|
| Ollama (qwen3:30b) | 11434 | Healthy — 18 GB model on RTX 3090. This is the only chat model in the stack (see Agent Node below) |
| **wyoming-identify-proxy** | **10300 (host-facing)** | Healthy — transparent Wyoming ASR relay in front of the real Whisper server (see below), also taps audio for speaker ID. HA talks to this now, unchanged from its own point of view |
| wyoming-whisper | 10300 (internal only) | Healthy, and **actually transcribes now** — built from a local wrapper (`servers/inference/wyoming-whisper/Dockerfile`) adding CUDA runtime libs the upstream image never bundled. Confirmed live: every real transcription crashed with a missing-`libcublas` error until this fix; no earlier test in this project exercised real audio, only HA's text-input intent stage, so it went unnoticed. No longer has a host port — reached only via `wyoming-identify-proxy` |
| speechbrain-speaker-id | 8200 | Healthy — `POST /enroll`, `POST /identify`, `GET/POST /config` (runtime-configurable match threshold), `DELETE /speakers/{id}`. Enrollment UI at `enroll.michaelgoldstein.co` (see Custom Software table) |
| Piper TTS | 10200 | Healthy — TTS, wired in as `tts.piper` (voice: `en_US-lessac-medium`, the model actually installed on the container — check before changing `tts_voice` in the pipeline, a mismatch fails silently) |
| OpenWakeWord | 10400 | Healthy — wired in as `wake_word.openwakeword` |
| Metrics exporters | 9100/9835/9091 | Healthy — scraped by agent Prometheus |

**Speaker ID in the voice pipeline (Option A, 2026-09-17):** `wyoming-identify-proxy`
taps every STT call's audio for `speechbrain`'s `/identify`, caching the result in a
single **time-based** "last speaker" slot (`GET :8300/last-speaker`, ~8s TTL) — not
per-satellite. Confirmed against home-assistant/core's actual `wyoming/stt.py` before
building anything: HA's Wyoming STT call carries no device/satellite identifier
whatsoever, so per-satellite correlation isn't possible at this protocol layer without
running one proxy instance per satellite (Option A2, not built). `agent-orchestrator`'s
`/ha/conversation/process` reads that endpoint per request; a confident, recent match
promotes the request to that speaker's personal scope, otherwise it falls back to the
original family-scope default. Accepted tradeoff: a same-second cross-satellite mixup
is possible in theory, in exchange for not needing N proxies + N separate HA pipelines.

HA's own Wyoming integrations for these three were added via
`Settings → Devices & Services → Add Integration → Wyoming Protocol`
(host `192.168.13.15`, the three ports above), and the default Assist
pipeline's `stt_engine`/`tts_engine`/`wake_word_entity` point at the
resulting entities. HA also has pre-existing `hassio`-sourced Piper/Whisper
add-ons from before this project (`stt.faster_whisper_2`, `tts.piper_2`) —
those are unrelated and unused now, not a duplicate to clean up urgently,
just don't confuse the two when looking at HA's integration list.

---

### Agent Node — Phase 2 Deployed ✅, fully healthy

| Service | Status | Notes |
|---------|--------|-------|
| ollama-agent | Up (healthcheck cosmetic) | Only hosts `nomic-embed-text:v1.5` for embeddings now. `qwen3:4b`/`qwen3:8b`/`qwen3:1.7b` weights are still present on disk (pulled previously) but **not referenced by `litellm_config.yaml` and not in the routing path** — the 4b/8b tiers were removed in PR #11; don't assume their presence on disk means they're live. |
| LiteLLM | Up (Docker healthcheck shows unhealthy — cosmetic, see note below) | Two models: `assistant` (`qwen3:30b`, routed to the **inference node's** RTX 3090 at `inference.home.local:11434`, not local) and `embeddings` (`nomic-embed-text:v1.5`, local at `ollama:11434`) |
| Prometheus | Healthy | Scrapes agent + inference node |
| Grafana | Up | grafana.michaelgoldstein.co |
| Qdrant | Healthy | Vector DB for agent memory — port 6333 |
| **agent-orchestrator** | **Healthy, `/health` → `"status": "ok"`** | Port 8100 / `agent-api.michaelgoldstein.co`. All 6 configured MCP servers connected (`ha-mcp`, `mcp-memory-scoped`, `mcp-notifications`, `mcp-workflow-status`, `mcp-calendar`, `google-workspace`), 60 tools total |
| **Open WebUI** | **Healthy** | Port 3000 — chat UI wired to LiteLLM |

**LiteLLM healthcheck note:** Docker reports `(unhealthy)` because LiteLLM's
built-in `/health` probe runs a live inference call to warm-check each model,
and that probe alone is flaky enough to occasionally miss its window — actual
chat completions and 768-dim embeddings both work correctly. Same story for
`ollama-agent`'s cosmetic `(unhealthy)`.

**`ha-mcp` note (fixed 2026-09-17):** the SSE URL was `${HA_URL}/mcp`, which
404s — HA's built-in **MCP Server** integration (present in HA core, just
never added via `Settings → Devices & Services`) actually serves SSE at
`/mcp_server/sse`. Fixed in `mcp_servers.json`; the integration itself was
added to HA via its config-entries API.

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

## Custom Software — Deployed and Running

All source lives in `custom-software/`. The agent-orchestrator image is built
directly on the agent node (`docker build` from the `custom-software/`
context, not GHCR pull, though the images are also published there by CI on
push to `main`).

| Package | What it does | Status |
|---------|-------------|--------|
| `agent-orchestrator` | FastAPI + LangGraph agent. Handles chat, memory, tool dispatch, HA conversation protocol, WebSocket streaming. Three sync modes: simple (single-pass), multistep (planned steps), interactive (HITL) — plus an async `research` mode. | Deployed, healthy |
| `mcp-memory-scoped` | 7-tool MCP server backed by Qdrant. Scoped memory (personal vs. family) with auto-promotion for important facts. | Deployed, connected |
| `mcp-notifications` | 4-tool MCP server. HA-native push to mobile, persistent notifications, TTS on any media player, Open WebUI message injection. | Deployed, connected — needs `HA_URL`/`HA_TOKEN` in its stdio subprocess `env` block in `mcp_servers.json` (stdio subprocesses do **not** inherit the container's full environment, only a safe allowlist — see note below) |
| `mcp-workflow-status` | 5-tool MCP server. Check, resume, and cancel async research tasks via the orchestrator REST API. | Deployed, connected |
| `mcp-calendar` | 4-tool MCP server. Read/write HA Google Calendar via HA's REST API (`CALENDAR_PROVIDER=ha`, default). Extensible provider pattern — add CalDAV etc. by dropping a file in `providers/`. `list_events` tolerates an LLM-guessed `calendar_id` by aggregating all accessible calendars instead of failing. | Deployed, connected, verified against real calendar data |
| `google-workspace` | Gmail/Docs/Drive via `taylorwilsdon/workspace-mcp` (PyPI). Deliberately excludes `calendar` from its `--tools` list — `mcp-calendar` is the canonical calendar path, not this. | Deployed; connects even without Google OAuth credentials configured (degrades gracefully, no Gmail/Docs/Drive tools until set up) |
| `speechbrain-speaker-id` | HTTP service for per-speaker voice identification via ECAPA-TDNN embeddings + Qdrant. `POST /enroll`, `POST /identify`, `GET /speakers`, `DELETE /speakers/{id}`, `GET`/`POST /config` (runtime-configurable match threshold, persisted). | Deployed on the inference node (`:8200`), healthy, and live in the real voice pipeline via `wyoming-identify-proxy` (see below). Decodes WAV/FLAC/OGG only (not AAC/MP3/M4A — convert client-side first) |
| `speaker-enroll` | Mobile-friendly web UI to record (`MediaRecorder`/`getUserMedia`) or upload a voice sample, name it, and enroll. Converts anything ffmpeg reads (mp4/m4a/mp3/webm/...) to the WAV speechbrain decodes; proxies to speechbrain server-side so the browser never talks to it directly. | Deployed, exposed at `enroll.michaelgoldstein.co` (same trust model as every other exposed service — no extra IP restriction, worth reconsidering given it's biometric enrollment) |
| `wyoming-identify-proxy` | Transparent Wyoming ASR relay between HA and the real Whisper server; taps the audio for speechbrain `/identify` in the background. `GET :8300/last-speaker` (time-based, ~8s TTL — see note above for why not per-satellite). | Deployed, healthy, verified live with a real spoken recording (correct transcription + correct speaker match) |
| `ha_custom_component` (`ha_jarvis`) | Home Assistant custom conversation agent. Bridges HA's Assist pipeline to `agent-orchestrator`: tries HA's built-in intent matching first (fast local device control), then `POST {base_url}/ha/conversation/process` for everything else. | Deployed on the HA VM (`/config/custom_components/ha_jarvis`), registered, and set as the **default** Assist pipeline's conversation engine. Verified end-to-end through the real pipeline mechanism, not just direct API calls |

**Stdio MCP subprocess environment note:** `agent-orchestrator` spawns
`mcp-calendar`/`mcp-notifications`/etc. as subprocesses over stdio. The MCP
SDK's `env=None` default does **not** mean "inherit the parent container's
environment" — it merges only a small safe allowlist (`PATH`, `HOME`, etc.,
see `mcp.client.stdio.get_default_environment`), deliberately never leaking
secrets into a spawned subprocess. Any stdio server needing `HA_TOKEN` or
similar must declare it explicitly in that server's `"env"` block in
`mcp_servers.json`, and those values go through the same `${VAR}` expansion
as `url`/`headers`. Confirmed live: this was silently broken for
`mcp-calendar` and `mcp-notifications` until fixed 2026-09-17.

**MCP servers enabled when agent-orchestrator starts:** `ha-mcp`,
`mcp-memory-scoped`, `mcp-notifications`, `mcp-workflow-status`,
`mcp-calendar`, `google-workspace`. `mcp-playwright` stays disabled — no
`playwright-mcp` container exists yet, that's more than a config flag.
`mcp-filesystem`/`mcp-fetch` wait on Phase 3 (NAS NFS mounts). `mcp-shopping-list`/`mcp-routines` are Phase 4 placeholders.

---

## Redeploying / Making Changes

Deploy dirs on each node are standalone (`/opt/homelab-ai/<node>/`), rsynced
from this repo — **not** git checkouts. There's no single "deploy everything"
button for the custom software; each piece is synced and restarted
individually.

### agent-orchestrator (code or config change)

```bash
rsync -avz --exclude='.venv' --exclude='__pycache__' custom-software/ \
  michael@192.168.13.22:/opt/homelab-ai/custom-software/

# mcp_servers.json has a SEPARATE runtime copy, bind-mounted — a plain
# custom-software/ sync does NOT touch it. Copy explicitly if it changed:
ssh michael@192.168.13.22 \
  "cp /opt/homelab-ai/custom-software/agent-orchestrator/config/mcp_servers.json \
      /opt/homelab-ai/agent/config/mcp_servers.json"

ssh michael@192.168.13.22 \
  "cd /opt/homelab-ai/custom-software/agent-orchestrator && \
   docker build --no-cache -t agent-orchestrator:latest -f Dockerfile .."

ssh michael@192.168.13.22 \
  "docker tag agent-orchestrator:latest ghcr.io/brainstain/ha-jarvis/agent-orchestrator:latest && \
   cd /opt/homelab-ai/agent && docker compose up -d --no-deps --force-recreate agent-orchestrator"
```

`--force-recreate` is required — a plain `docker compose up -d` (or
`docker restart`) does **not** pick up a newly built image tag if the
container's already running; it silently keeps serving the old image.
Confirmed live: this cost a full debugging detour before being caught via
`docker inspect <container> --format '{{.Image}}'` not matching the freshly
built image ID.

### Gateway (Caddyfile / DNS changes)

```bash
rsync -av servers/gateway/ michael@192.168.13.29:/opt/homelab-ai/gateway/
ssh michael@192.168.13.29 "cd /opt/homelab-ai/gateway && \
  docker compose up -d --no-deps --force-recreate caddy pihole"
```

A brand-new subdomain's first ACME cert request can transiently fail on
Let's Encrypt with `No TXT record found` (DNS propagation lag) — Caddy
automatically retries via ZeroSSL and it resolves within ~10-20 seconds. Not
a configuration error if you see it once.

### Smoke test

```bash
curl https://agent-api.michaelgoldstein.co/health

curl -X POST https://agent-api.michaelgoldstein.co/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What can you help me with?", "scope": "family", "user_id": "test"}'
```

---

## What's Left

| Item | Status |
|------|--------|
| HA custom conversation component | **Done** — `ha_jarvis` deployed, registered, set as default pipeline's conversation agent |
| Wire HA Wyoming voice pipeline | **Done** — Whisper/Piper/OpenWakeWord integrations added, default pipeline updated |
| Get Open WebUI API key from admin panel | **Done** |
| Enable `agent-api.michaelgoldstein.co` | **Done** |
| Configure NFS on Synology DSM | Open — unlocks Phase 3 (Paperless) |
| Phase 3 deploy | Open — `docker compose --profile phase3 up -d` on agent node, blocked on NFS |
| SpeechBrain speaker enrollment + voice wiring | **Done** — enrollment UI live, speaker ID wired into the real voice pipeline (Option A), verified with a real spoken recording. Other family members can self-enroll via `enroll.michaelgoldstein.co` |
| Authentik gating other services | Open — Authentik itself is deployed and healthy; deciding which services to protect and wiring `forward_auth` for them is the remaining work |
