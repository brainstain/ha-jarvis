# Monitoring

Prometheus + Grafana run on the agent node and scrape all three homelab
nodes (gateway, agent, inference). NAS is storage-only and isn't scraped.

- Grafana: `https://grafana.michaelgoldstein.co` (also `agent:3100`)
- Prometheus: `https://prometheus.michaelgoldstein.co` (also `agent:9090`)
- Config lives in `servers/agent/config/prometheus.yml`, `alerts.yml`,
  and `grafana/provisioning/`

## What's scraped

| Job | Target | Node | What it covers |
|---|---|---|---|
| `prometheus` | `localhost:9090` | agent | self-monitoring |
| `node-agent` / `node-inference` / `node-gateway` | `node-exporter:9100` | all 3 | CPU, memory, disk, filesystem |
| `gpu-agent` / `gpu-inference` | `nvidia-gpu-exporter:9835` | agent, inference | GPU temp, VRAM, power, utilization |
| `ollama-agent` / `ollama-inference` | `ollama-metrics` sidecar | agent, inference | Ollama loaded-model count only |
| `qdrant` | `qdrant:6333/metrics` | agent | vector DB collections, points, memory |
| `litellm` | `litellm:4000/metrics` | agent | proxy requests, latency, tokens, spend, per-deployment health |
| `agent-orchestrator` | `agent-orchestrator:8100/metrics` | agent | chat requests/latency, LLM latency, MCP tool use, router fallbacks |

Gateway (Caddy, Pi-hole, Authentik, Uptime Kuma) has no `/metrics`
exporters wired up — only its node-level OS metrics are scraped. Uptime
Kuma independently does HTTP/TCP health checks on all services; it's a
separate signal, not fed into Prometheus.

## Dashboards

Provisioned automatically from `servers/agent/config/grafana/provisioning/dashboards/`
— editing a `.json` file there and redeploying updates the dashboard.

- **Homelab Overview** — service up/down, GPU temp/VRAM, node CPU/memory/disk
  across all 3 nodes, Ollama loaded-model count
- **GPU & Inference** — per-GPU utilization, temperature, VRAM, power draw,
  PCIe link width (agent's GTX 1080 Ti, inference's RTX 3090)
- **Agent Services** — agent-orchestrator chat request rate/latency, LLM call
  latency by model, MCP tool invocation rate, router fallback rate, Qdrant
  point counts and memory, LiteLLM request rate and latency

## Alerting

`alerts.yml` defines rules (GPU temp warning/critical, high memory, low disk,
scrape target down) and Prometheus evaluates them, but **no Alertmanager is
deployed** — firing alerts are only visible in Prometheus's own `/alerts`
page, nothing pages or notifies yet. Routing alerts to the HA notification
service is tracked as open work.

## Known limitations

- **Ollama request-level metrics don't exist.** The `ollama-metrics` sidecar
  (`ghcr.io/norskhelsenett/ollama-metrics`) is a minimal proxy that only
  exposes `ollama_loaded_models` — no request latency or throughput. Use the
  Agent Services dashboard's LLM-latency panel instead (measured at the
  LiteLLM/orchestrator layer, which wraps every Ollama call).
- **The `ollama-metrics` image listens on port 8080 internally**, not 9091 —
  the compose files map host port `9091:8080` for external/cross-node access
  (e.g. `inference.home.local:9091`), but same-Docker-network scrapes must
  target the container's real port directly (`ollama-metrics:8080`). Getting
  this wrong silently produces a permanently-down scrape target with no
  obvious error beyond "connection refused" in Prometheus's target list.
- **LiteLLM's `/metrics` requires `litellm_settings.callbacks: ["prometheus"]`**
  in `litellm_config.yaml` — without it the endpoint exists but returns an
  empty body.
- **The Prometheus datasource's UID is pinned to the auto-generated
  `PBFA97CFB590B2093`, not a friendly name.** `datasource.yml` never set an
  explicit `uid`, so Grafana generated a random one on first provisioning;
  every dashboard panel's `datasource.uid` has to match it exactly or panels
  silently show no data (Grafana finds the datasource fine via its own UI,
  the mismatch only breaks the specific hardcoded reference in dashboard
  JSON). Do **not** try to fix this by adding `uid: prometheus` to
  `datasource.yml` and restarting — Grafana's provisioning reconciler treats
  an in-place UID change on an existing datasource as "data source not
  found" and crash-loops. If this ever needs to be cleaned up: set
  `editable: true` in `datasource.yml`, redeploy, delete the datasource via
  `DELETE /api/datasources/uid/<old-uid>`, then redeploy with the desired
  pinned `uid` and `editable: false` — the datasource gets created fresh
  instead of updated in place. Until then, any new dashboard panel must use
  `"datasource": {"type": "prometheus", "uid": "PBFA97CFB590B2093"}`
  verbatim (check `GET /api/datasources` on the live instance if this ever
  changes, e.g. after a `grafana_data` volume wipe).
- Redis (gateway) and Caddy/Pi-hole/Authentik have no exporters — only
  covered indirectly via Uptime Kuma's HTTP checks and gateway's node-exporter.
