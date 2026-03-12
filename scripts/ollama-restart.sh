#!/bin/bash
set -euo pipefail

# ============================================================
# Ollama Daily Restart — OOM Mitigation (Gap 39)
# Schedule: 0 4 * * * /opt/homelab-ai/scripts/ollama-restart.sh
# ============================================================

LOG_FILE="/var/log/ollama-restart.log"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"; }

# Restart Inference Engine Ollama (primary, RTX 3090)
if docker ps -q -f name=ollama-inference > /dev/null 2>&1; then
    log "Restarting ollama-inference..."
    docker restart ollama-inference
    sleep 15
    if curl -sf --connect-timeout 5 http://localhost:11434/api/tags > /dev/null 2>&1; then
        log "  ollama-inference healthy after restart"
    else
        log "  WARNING: ollama-inference did not become healthy"
    fi
fi

# Restart Agent Node Ollama (secondary, GTX 1080 Ti)
if docker ps -q -f name=ollama-agent > /dev/null 2>&1; then
    log "Restarting ollama-agent..."
    docker restart ollama-agent
    sleep 15
    if curl -sf --connect-timeout 5 http://localhost:11434/api/tags > /dev/null 2>&1; then
        log "  ollama-agent healthy after restart"
    else
        log "  WARNING: ollama-agent did not become healthy"
    fi
fi

log "Ollama restart complete."
