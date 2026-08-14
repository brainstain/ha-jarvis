#!/bin/bash
set -euo pipefail

# ============================================================
# GPU Thermal Response — power-limit cap (RTX 3090)
#
# Called from an Alertmanager webhook on GPUTemperatureCritical
# (or run manually). Power capping is instant, needs no container
# restart, and drops no in-flight requests — unlike changing
# OLLAMA_NUM_PARALLEL, which forces a restart + 30-90s model reload.
#
# Usage:
#   gpu-thermal-cap.sh cap       # apply reduced power limit
#   gpu-thermal-cap.sh restore   # restore default power limit
#   gpu-thermal-cap.sh status    # show current limit + temperature
#
# Run inside the vm-inference guest (where the driver lives).
# ============================================================

CAP_WATTS="${CAP_WATTS:-250}"        # reduced limit under thermal pressure
DEFAULT_WATTS="${DEFAULT_WATTS:-350}" # 3090 stock power limit
LOG_FILE="/var/log/gpu-thermal-cap.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"; }

case "${1:-status}" in
  cap)
    log "Applying GPU power cap: ${CAP_WATTS}W"
    nvidia-smi -pm 1 > /dev/null
    nvidia-smi -pl "$CAP_WATTS"
    log "  temp now: $(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader)C"
    ;;
  restore)
    log "Restoring GPU power limit: ${DEFAULT_WATTS}W"
    nvidia-smi -pl "$DEFAULT_WATTS"
    ;;
  status)
    nvidia-smi --query-gpu=temperature.gpu,power.draw,power.limit --format=csv
    ;;
  *)
    echo "Usage: $0 {cap|restore|status}" >&2
    exit 1
    ;;
esac
