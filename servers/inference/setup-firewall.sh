#!/bin/bash
set -euo pipefail

# ============================================================
# Inference Engine — ufw firewall rules
#
# Enforces the flow table in servers/inference/SERVER_SPEC.md.
# Run inside the vm-inference guest as root. Reads GATEWAY_IP
# and AGENT_IP from the repo .env (or export them beforehand).
#
# NOTE: Ollama has no auth — until LiteLLM is the only exposed
# path, these rules are the only thing preventing arbitrary LAN
# clients from using (or deleting) models on :11434.
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/../../.env}"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a; source "$ENV_FILE"; set +a
fi

: "${GATEWAY_IP:?Set GATEWAY_IP (in .env or environment)}"
: "${AGENT_IP:?Set AGENT_IP (in .env or environment)}"

echo "==> Configuring ufw (gateway=${GATEWAY_IP}, agent=${AGENT_IP})"

ufw --force reset
ufw default deny incoming
ufw default allow outgoing

# SSH (management) — whole LAN; tighten to a mgmt IP if desired
ufw allow 22/tcp comment 'SSH management'

# Agent (LiteLLM) -> Ollama
ufw allow from "$AGENT_IP" to any port 11434 proto tcp comment 'LiteLLM -> Ollama'

# Gateway (HA) -> voice pipeline (Wyoming)
ufw allow from "$GATEWAY_IP" to any port 10300 proto tcp comment 'HA -> wyoming-whisper STT'
ufw allow from "$GATEWAY_IP" to any port 10200 proto tcp comment 'HA -> Piper TTS'
ufw allow from "$GATEWAY_IP" to any port 10400 proto tcp comment 'HA -> openWakeWord'

# Agent (Orchestrator) -> SpeechBrain (Phase 2)
ufw allow from "$AGENT_IP" to any port 8200 proto tcp comment 'Orchestrator -> SpeechBrain'

# Agent (Prometheus) -> exporters
ufw allow from "$AGENT_IP" to any port 9091 proto tcp comment 'Prometheus -> ollama-metrics'
ufw allow from "$AGENT_IP" to any port 9100 proto tcp comment 'Prometheus -> node-exporter'
ufw allow from "$AGENT_IP" to any port 9835 proto tcp comment 'Prometheus -> gpu-exporter'

ufw --force enable
ufw status verbose
