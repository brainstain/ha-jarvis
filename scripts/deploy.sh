#!/bin/bash
set -euo pipefail

# ============================================================
# Homelab AI Agent — Master Deployment Script
# Run from any node with SSH access to all others.
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# ── Configuration (override via .env or export) ─────────────
GATEWAY_HOST="${GATEWAY_HOST:-gateway.home.local}"
AGENT_HOST="${AGENT_HOST:-agent.home.local}"
INFERENCE_HOST="${INFERENCE_HOST:-inference.home.local}"
NAS_HOST="${NAS_HOST:-nas.home.local}"
SSH_USER="${SSH_USER:-root}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/homelab-ai}"

# ── Colors ───────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# ── Helpers ──────────────────────────────────────────────────
ssh_cmd() {
    local host="$1"; shift
    ssh -o ConnectTimeout=5 "${SSH_USER}@${host}" "$@"
}

deploy_node() {
    local host="$1"
    local node_dir="$2"
    local node_name="$3"

    info "Deploying ${node_name} to ${host}..."
    
    # Create deploy directory
    ssh_cmd "$host" "mkdir -p ${DEPLOY_DIR}/${node_name}"
    
    # Sync files
    rsync -avz --delete \
        "${PROJECT_ROOT}/servers/${node_dir}/" \
        "${SSH_USER}@${host}:${DEPLOY_DIR}/${node_name}/"
    
    # Sync shared config
    rsync -avz \
        "${PROJECT_ROOT}/config/" \
        "${SSH_USER}@${host}:${DEPLOY_DIR}/${node_name}/config/" \
        2>/dev/null || true
    
    # Copy .env if exists
    if [[ -f "${PROJECT_ROOT}/.env" ]]; then
        rsync -avz "${PROJECT_ROOT}/.env" \
            "${SSH_USER}@${host}:${DEPLOY_DIR}/${node_name}/.env"
    fi
    
    info "${node_name} files synced."
}

start_node() {
    local host="$1"
    local node_name="$2"
    
    info "Starting ${node_name} services..."
    ssh_cmd "$host" "cd ${DEPLOY_DIR}/${node_name} && docker compose pull && docker compose up -d"
    info "${node_name} services started."
}

check_health() {
    local host="$1"
    local port="$2"
    local path="$3"
    local name="$4"
    local max_attempts="${5:-30}"
    
    for i in $(seq 1 "$max_attempts"); do
        if curl -sf --connect-timeout 2 "http://${host}:${port}${path}" > /dev/null 2>&1; then
            info "${name} is healthy ✓"
            return 0
        fi
        sleep 2
    done
    warn "${name} did not become healthy after ${max_attempts} attempts"
    return 1
}

# ── Commands ─────────────────────────────────────────────────
cmd_deploy_all() {
    info "═══ Full System Deployment ═══"
    
    # 1. Gateway first (DNS, Redis, HA — other nodes depend on these)
    deploy_node "$GATEWAY_HOST" "gateway" "gateway"
    start_node "$GATEWAY_HOST" "gateway"
    check_health "$GATEWAY_HOST" 53 "" "Pi-hole DNS" 15
    check_health "$GATEWAY_HOST" 6379 "" "Redis" 10
    check_health "$GATEWAY_HOST" 8123 "/api/" "Home Assistant" 30
    
    # 2. Inference Engine (LLM, STT, TTS — agent depends on this)
    deploy_node "$INFERENCE_HOST" "inference" "inference"
    start_node "$INFERENCE_HOST" "inference"
    check_health "$INFERENCE_HOST" 11434 "/api/tags" "Ollama (3090)" 60
    check_health "$INFERENCE_HOST" 8443 "/health" "faster-whisper" 30
    
    # 3. Agent Node (orchestrator, Qdrant, WebUI)
    deploy_node "$AGENT_HOST" "agent" "agent"
    
    # Build custom software if needed
    info "Building custom software images..."
    rsync -avz "${PROJECT_ROOT}/custom-software/" \
        "${SSH_USER}@${AGENT_HOST}:${DEPLOY_DIR}/custom-software/"
    
    start_node "$AGENT_HOST" "agent"
    check_health "$AGENT_HOST" 6333 "/healthz" "Qdrant" 15
    check_health "$AGENT_HOST" 4000 "/health" "LiteLLM" 30
    check_health "$AGENT_HOST" 8100 "/health" "Agent Orchestrator" 30
    check_health "$AGENT_HOST" 3000 "" "Open WebUI" 30
    
    info "═══ Deployment Complete ═══"
    echo ""
    info "Services:"
    info "  Home Assistant:  http://${GATEWAY_HOST}:8123"
    info "  Open WebUI:      http://${AGENT_HOST}:3000"
    info "  Grafana:         http://${AGENT_HOST}:3100"
    info "  Uptime Kuma:     http://${GATEWAY_HOST}:3001"
    info "  LiteLLM:         http://${AGENT_HOST}:4000"
}

cmd_deploy_node() {
    local node="$1"
    case "$node" in
        gateway)   deploy_node "$GATEWAY_HOST" "gateway" "gateway"; start_node "$GATEWAY_HOST" "gateway" ;;
        agent)     deploy_node "$AGENT_HOST" "agent" "agent"; start_node "$AGENT_HOST" "agent" ;;
        inference) deploy_node "$INFERENCE_HOST" "inference" "inference"; start_node "$INFERENCE_HOST" "inference" ;;
        *) error "Unknown node: $node. Use: gateway, agent, inference" ;;
    esac
}

cmd_status() {
    info "═══ System Status ═══"
    echo ""
    
    info "Gateway (${GATEWAY_HOST}):"
    check_health "$GATEWAY_HOST" 53 "" "  Pi-hole" 3 || true
    check_health "$GATEWAY_HOST" 6379 "" "  Redis" 3 || true
    check_health "$GATEWAY_HOST" 8123 "/api/" "  Home Assistant" 3 || true
    check_health "$GATEWAY_HOST" 8888 "/healthz" "  SearXNG" 3 || true
    check_health "$GATEWAY_HOST" 3001 "" "  Uptime Kuma" 3 || true
    echo ""
    
    info "Inference (${INFERENCE_HOST}):"
    check_health "$INFERENCE_HOST" 11434 "/api/tags" "  Ollama (3090)" 3 || true
    check_health "$INFERENCE_HOST" 8443 "/health" "  faster-whisper" 3 || true
    check_health "$INFERENCE_HOST" 10200 "" "  Piper TTS" 3 || true
    echo ""
    
    info "Agent (${AGENT_HOST}):"
    check_health "$AGENT_HOST" 6333 "/healthz" "  Qdrant" 3 || true
    check_health "$AGENT_HOST" 4000 "/health" "  LiteLLM" 3 || true
    check_health "$AGENT_HOST" 11434 "/api/tags" "  Ollama (1080 Ti)" 3 || true
    check_health "$AGENT_HOST" 8100 "/health" "  Agent Orchestrator" 3 || true
    check_health "$AGENT_HOST" 3000 "" "  Open WebUI" 3 || true
    check_health "$AGENT_HOST" 9090 "/-/healthy" "  Prometheus" 3 || true
    check_health "$AGENT_HOST" 3100 "/api/health" "  Grafana" 3 || true
}

cmd_pull_models() {
    info "Pulling models on Inference Engine (3090)..."
    ssh_cmd "$INFERENCE_HOST" "docker exec ollama-primary ollama pull qwen3:30b-a3b-q5_K_M"
    
    info "Pulling models on Agent Node (1080 Ti)..."
    ssh_cmd "$AGENT_HOST" "docker exec ollama-agent ollama pull qwen3:4b-q8_0"
    ssh_cmd "$AGENT_HOST" "docker exec ollama-agent ollama pull nomic-embed-text:v1.5"
    ssh_cmd "$AGENT_HOST" "docker exec ollama-agent ollama pull qwen3:8b-q8_0"
    
    info "All models pulled."
}

cmd_backup() {
    info "═══ Running Backups ═══"
    
    # Qdrant snapshot
    info "Creating Qdrant snapshot..."
    curl -sf -X POST "http://${AGENT_HOST}:6333/snapshots" > /dev/null && info "  Qdrant snapshot created" || warn "  Qdrant snapshot failed"
    
    # LangGraph SQLite backup
    info "Backing up LangGraph state..."
    ssh_cmd "$AGENT_HOST" "docker exec agent-orchestrator sqlite3 /data/langgraph/checkpoints.db '.backup /data/langgraph/checkpoints_backup.db'"
    ssh_cmd "$AGENT_HOST" "rsync -az ${DEPLOY_DIR}/agent/data/langgraph/ ${NAS_HOST}:/volume1/backups/langgraph/"
    
    # HA config backup
    info "Backing up Home Assistant..."
    ssh_cmd "$GATEWAY_HOST" "cd ${DEPLOY_DIR}/gateway && tar czf /tmp/ha_backup.tar.gz homeassistant_config/"
    ssh_cmd "$GATEWAY_HOST" "rsync -az /tmp/ha_backup.tar.gz ${NAS_HOST}:/volume1/backups/homeassistant/"
    
    info "Backups complete."
}

cmd_logs() {
    local node="$1"
    local service="${2:-}"
    case "$node" in
        gateway)   ssh_cmd "$GATEWAY_HOST" "cd ${DEPLOY_DIR}/gateway && docker compose logs -f --tail=100 ${service}" ;;
        agent)     ssh_cmd "$AGENT_HOST" "cd ${DEPLOY_DIR}/agent && docker compose logs -f --tail=100 ${service}" ;;
        inference) ssh_cmd "$INFERENCE_HOST" "cd ${DEPLOY_DIR}/inference && docker compose logs -f --tail=100 ${service}" ;;
        *) error "Unknown node: $node" ;;
    esac
}

# ── Main ─────────────────────────────────────────────────────
case "${1:-help}" in
    deploy)
        if [[ -n "${2:-}" ]]; then
            cmd_deploy_node "$2"
        else
            cmd_deploy_all
        fi
        ;;
    status)       cmd_status ;;
    pull-models)  cmd_pull_models ;;
    backup)       cmd_backup ;;
    logs)         cmd_logs "${2:-agent}" "${3:-}" ;;
    help|*)
        echo "Usage: $0 <command> [args]"
        echo ""
        echo "Commands:"
        echo "  deploy              Deploy all nodes in dependency order"
        echo "  deploy <node>       Deploy single node (gateway|agent|inference)"
        echo "  status              Health check all services"
        echo "  pull-models         Pull LLM models on all GPU nodes"
        echo "  backup              Run backup of all stateful data"
        echo "  logs <node> [svc]   Tail logs for a node or specific service"
        echo ""
        echo "Environment:"
        echo "  GATEWAY_HOST    (default: gateway.home.local)"
        echo "  AGENT_HOST      (default: agent.home.local)"
        echo "  INFERENCE_HOST  (default: inference.home.local)"
        echo "  SSH_USER        (default: root)"
        ;;
esac
