# Server Spec: Gateway Node (Mini PC)

**Hardware:** 4-core CPU, 16 GB RAM, 512 GB SSD  
**Role:** Network gateway, authentication, DNS, Home Assistant, monitoring  
**Always-on:** Yes (low power ~15W)  
**Proxmox VM:** `vm-gateway` — 4 vCPU, 12 GB RAM, 256 GB disk

---

## Services

| Service | Image | Port | RAM | Purpose |
|---------|-------|------|-----|---------|
| Caddy | caddy:2-alpine | 80, 443 | 50 MB | Reverse proxy, auto-HTTPS |
| Authentik | goauthentik/server | 9000 | 800 MB | SSO, user profiles, MFA |
| Authentik Worker | goauthentik/server | — | 400 MB | Background tasks |
| PostgreSQL (Authentik) | postgres:16-alpine | 5432 | 200 MB | Authentik database |
| Redis | redis:7-alpine | 6379 | 100 MB | Sessions, cache, Celery broker |
| Home Assistant | ghcr.io/home-assistant/home-assistant | 8123 | 1 GB | Smart home automation |
| Pi-hole | pihole/pihole | 53, 8053 | 150 MB | Local DNS + ad blocking |
| SearXNG | searxng/searxng | 8888 | 200 MB | Privacy-respecting metasearch |
| Uptime Kuma | louislam/uptime-kuma | 3001 | 150 MB | Service health monitoring |
| NUT Server | instantlinux/nut-upsd | 3493 | 20 MB | UPS monitoring |

**Total estimated RAM:** ~3.1 GB (well within 12 GB allocation)

---

## Key Configuration Notes

### Caddy
Acts as the single entry point for all web-facing services across all nodes. Routes to backend services on other nodes via internal DNS.

### Authentik
User profiles contain:
- `user_id`: unique identifier mapped from SpeechBrain speaker enrollment
- `role`: "adult" | "child"
- `age`: integer (for content filtering)
- `scope_default`: "personal" | "family"
- `home_location`: lat/lon for weather and location-aware queries
- `preferences`: JSON blob for macro targets, display preferences, etc.

### Home Assistant
- Voice pipeline configured with Wyoming protocol pointing to Inference Engine
- Conversation agent configured to route through Agent Orchestrator (not built-in Ollama)
- Fallback automation: polls Ollama health every 60s, switches to built-in intents on failure
- Shopping list integration enabled for mcp-shopping-list

### Pi-hole
- Local DNS records for all internal services (e.g., `inference.home.local`, `agent.home.local`)
- Conditional forwarding for homelab domain
- Fallback upstream: Cloudflare (1.1.1.1) + Quad9 (9.9.9.9)

### Redis
Serves three roles:
1. Authentik session store
2. Celery message broker (task queue for async agent jobs)
3. General caching layer

### NUT
- Monitors UPS via USB
- Configured to initiate graceful shutdown of all Proxmox nodes at 5 minutes remaining battery
- Sends HA notification when power event detected

---

## Health Checks

| Service | Check | Interval | Timeout |
|---------|-------|----------|---------|
| Caddy | TCP :443 | 10s | 5s |
| Authentik | HTTP /api/v3/root/config/ | 30s | 10s |
| Redis | `redis-cli ping` | 10s | 5s |
| Home Assistant | HTTP /api/ | 30s | 10s |
| Pi-hole | TCP :53 | 10s | 5s |
| SearXNG | HTTP /healthz | 30s | 10s |
| Uptime Kuma | HTTP /api/status-page/heartbeat | 30s | 10s |

---

## Firewall Rules

| From | To | Port | Protocol | Purpose |
|------|-----|------|----------|---------|
| Internet | Caddy | 80, 443 | TCP | Web traffic |
| All nodes | Pi-hole | 53 | TCP/UDP | DNS |
| All nodes | Redis | 6379 | TCP | Cache/broker |
| All nodes | HA | 8123 | TCP | HA API |
| Agent Node | SearXNG | 8888 | TCP | Search queries |
| All nodes | NUT | 3493 | TCP | UPS status |
