#!/bin/bash
set -euo pipefail

# ============================================================
# Homelab AI Agent — Automated Backup Script
# Schedule via cron: 0 3 * * * /opt/homelab-ai/scripts/backup-cron.sh
# ============================================================

LOG_FILE="/var/log/homelab-backup.log"
NAS_BACKUP_DIR="/mnt/nas/backups/homelab-ai"
LOCAL_BACKUP_DIR="/opt/homelab-ai/backups"
RETENTION_DAYS=30
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"; }

mkdir -p "$LOCAL_BACKUP_DIR" "$NAS_BACKUP_DIR" 2>/dev/null || true

# ── 1. Qdrant Snapshots ─────────────────────────────────────
log "Creating Qdrant snapshots..."
for collection in memories documents conversations; do
    if curl -sf -X POST "http://localhost:6333/collections/${collection}/snapshots" > /dev/null 2>&1; then
        log "  ✓ ${collection} snapshot created"
    else
        log "  ✗ ${collection} snapshot failed"
    fi
done

# Copy latest snapshots to NAS
rsync -az /var/lib/docker/volumes/qdrant_data/_data/snapshots/ \
    "${NAS_BACKUP_DIR}/qdrant/${TIMESTAMP}/" 2>/dev/null || log "  ✗ Qdrant rsync failed"

# ── 2. LangGraph SQLite ─────────────────────────────────────
log "Backing up LangGraph checkpoints..."
docker exec agent-orchestrator sqlite3 /data/langgraph/checkpoints.db \
    ".backup /data/langgraph/checkpoints_${TIMESTAMP}.db" 2>/dev/null

docker cp "agent-orchestrator:/data/langgraph/checkpoints_${TIMESTAMP}.db" \
    "${LOCAL_BACKUP_DIR}/langgraph_${TIMESTAMP}.db" 2>/dev/null

cp "${LOCAL_BACKUP_DIR}/langgraph_${TIMESTAMP}.db" \
    "${NAS_BACKUP_DIR}/langgraph/" 2>/dev/null || log "  ✗ LangGraph rsync failed"

# Clean temp file inside container
docker exec agent-orchestrator rm -f "/data/langgraph/checkpoints_${TIMESTAMP}.db" 2>/dev/null
log "  ✓ LangGraph backed up"

# ── 3. Open WebUI Data ──────────────────────────────────────
log "Backing up Open WebUI..."
docker cp open-webui:/app/backend/data "${LOCAL_BACKUP_DIR}/openwebui_${TIMESTAMP}" 2>/dev/null
rsync -az "${LOCAL_BACKUP_DIR}/openwebui_${TIMESTAMP}/" \
    "${NAS_BACKUP_DIR}/openwebui/" 2>/dev/null || log "  ✗ WebUI rsync failed"
log "  ✓ Open WebUI backed up"

# ── 4. Grafana Dashboards ───────────────────────────────────
log "Backing up Grafana..."
rsync -az /var/lib/docker/volumes/grafana_data/ \
    "${NAS_BACKUP_DIR}/grafana/" 2>/dev/null || log "  ✗ Grafana rsync failed"
log "  ✓ Grafana backed up"

# ── 5. Prometheus Data (weekly only) ────────────────────────
if [[ $(date +%u) -eq 7 ]]; then
    log "Weekly Prometheus backup..."
    rsync -az /var/lib/docker/volumes/prometheus_data/ \
        "${NAS_BACKUP_DIR}/prometheus/" 2>/dev/null || log "  ✗ Prometheus rsync failed"
    log "  ✓ Prometheus backed up (weekly)"
fi

# ── 6. Cleanup Old Backups ──────────────────────────────────
log "Cleaning backups older than ${RETENTION_DAYS} days..."
find "$LOCAL_BACKUP_DIR" -type f -mtime +${RETENTION_DAYS} -delete 2>/dev/null
find "$NAS_BACKUP_DIR" -type d -empty -delete 2>/dev/null
log "  ✓ Cleanup complete"

log "═══ Backup complete ═══"
