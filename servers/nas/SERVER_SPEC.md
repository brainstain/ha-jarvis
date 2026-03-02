# Server Spec: Storage Node (Synology NAS)

**Hardware:** Synology DiskStation, 16 TB usable storage  
**Role:** Document management, media storage, backup target  
**Always-on:** Yes  
**Note:** Not a Proxmox VM — native Synology DSM with Docker (Container Manager)

---

## Services

| Service | Image | Port | RAM | Purpose |
|---------|-------|------|-----|---------|
| Paperless-NGX | ghcr.io/paperless-ngx/paperless-ngx | 8000 | 1 GB | Document management |
| Paperless PostgreSQL | postgres:16-alpine | 5432 | 200 MB | Paperless database |
| Paperless Redis | redis:7-alpine | 6380 | 50 MB | Paperless task broker |
| Paperless-AI | clusterzx/paperless-ai | 3030 | 200 MB | AI categorization + tagging |

**Total estimated RAM:** ~1.5 GB

---

## Paperless-NGX Configuration

Paperless-NGX is the document ingestion and management layer. Documents are consumed from a watched folder, OCR'd, and stored with metadata. The RAG pipeline on the Agent Node indexes Paperless documents into Qdrant via the Paperless API.

### Storage Paths

| Path | Location | NFS Export | Purpose |
|------|----------|------------|---------|
| /data/paperless/media | NAS local | Yes (read-only to Agent) | Original + archived documents |
| /data/paperless/consume | NAS local | Yes (read-write) | Drop folder for new documents |
| /data/paperless/data | NAS local | **No** | SQLite/PostgreSQL data (local only) |
| /data/paperless/export | NAS local | Yes (read-only) | Periodic exports for backup |

### Metadata Schema for RAG Integration

Every document in Paperless carries tags and custom fields that the RAG pipeline reads:

```
Custom Fields:
  - scope: "family" | "personal"     (required)
  - owner: user_id string            (required)
  - rag_indexed: boolean             (set by RAG pipeline after indexing)
  - rag_indexed_at: datetime         (last index timestamp)
```

**Tags for content categorization:**
- `motorcycle`, `home`, `financial`, `medical`, `recipes`, `kid`, `work`
- Tags drive RAG retrieval filtering (e.g., search "motorcycle" only queries motorcycle-tagged docs)

### Paperless-AI

Runs alongside Paperless-NGX. On document ingestion, it:
1. Sends the OCR'd text to LiteLLM (via Agent Node)
2. LLM suggests tags, title, correspondent, and custom field values
3. Auto-applies suggestions (configurable confidence threshold)

This means new documents are automatically categorized with scope and tags before the RAG pipeline indexes them.

---

## NFS Exports

```bash
# /etc/exports (Synology equivalent: Shared Folder > NFS Permissions)

# Paperless media — read-only for Agent Node RAG pipeline
/volume1/paperless/media    agent.home.local(ro,sync,no_subtree_check)

# Paperless consume — write for any node to drop documents
/volume1/paperless/consume  *.home.local(rw,sync,no_subtree_check)

# Backups — write for all nodes
/volume1/backups            *.home.local(rw,sync,no_subtree_check)

# Model archive — read-only, large file storage
/volume1/models             inference.home.local(ro,sync,no_subtree_check)
/volume1/models             agent.home.local(ro,sync,no_subtree_check)
```

### Mount Options on Client Nodes

```bash
# /etc/fstab on Agent/Inference/Gateway nodes

# Read-only mounts — safe with soft,intr
nas.home.local:/volume1/paperless/media  /mnt/nas/paperless-media  nfs  soft,intr,timeo=10,ro  0  0
nas.home.local:/volume1/models           /mnt/nas/models           nfs  soft,intr,timeo=10,ro  0  0

# Write mounts — for backups only (not databases)
nas.home.local:/volume1/backups          /mnt/nas/backups          nfs  soft,intr,timeo=10,rw  0  0
```

**Critical rules:**
- **NEVER** mount database directories (Qdrant, PostgreSQL, SQLite) over NFS
- **NEVER** use `hard` mount option — processes hang indefinitely on NAS failure
- Model weights should be **copied to local NVMe** for inference, not served over NFS

---

## Backup Targets

The NAS serves as the primary backup destination for all nodes:

| Source | Method | Schedule | NAS Path | Retention |
|--------|--------|----------|----------|-----------|
| Qdrant snapshots | API + rsync | Daily 2am | /backups/qdrant/ | 30 days |
| LangGraph SQLite | .backup + rsync | Hourly | /backups/langgraph/ | 7 days |
| HA config | git bundle + rsync | Daily 3am | /backups/homeassistant/ | 90 days |
| Paperless DB | pg_dump | Daily 1am | /backups/paperless-db/ | 30 days |
| Paperless export | Built-in exporter | Weekly | /paperless/export/ | 12 weeks |
| Docker volumes | Restic | Daily 4am | /backups/docker-volumes/ | 30 days |
| Grafana data | rsync | Daily 3am | /backups/grafana/ | 30 days |
| Authentik DB | pg_dump | Daily 1am | /backups/authentik-db/ | 30 days |
| VM snapshots | Proxmox Backup Server | Weekly Sun 1am | /backups/pbs/ | 4 weeks |

### Offsite Backup

Synology Hyper Backup or Restic on any node pushes weekly to:
- **Backblaze B2** ($0.006/GB/month) — encrypted, versioned
- Estimated monthly cost: ~$2-5 for critical data (excluding model weights)

---

## Health Monitoring

The NAS itself is monitored by Uptime Kuma on the Gateway:

| Check | Target | Interval |
|-------|--------|----------|
| HTTP | Paperless-NGX :8000 | 60s |
| TCP | NFS :2049 | 30s |
| TCP | PostgreSQL :5432 | 30s |
| ICMP | NAS IP | 15s |

### NFS Health Script (runs on client nodes)

```bash
#!/bin/bash
# /usr/local/bin/check-nfs-health.sh
# Cron: */1 * * * * (every minute)

NAS_MOUNTS=("/mnt/nas/paperless-media" "/mnt/nas/backups" "/mnt/nas/models")

for mount in "${NAS_MOUNTS[@]}"; do
    if ! timeout 5 stat "$mount" &>/dev/null; then
        echo "$(date): NFS mount $mount is stale, attempting remount"
        umount -l "$mount" 2>/dev/null
        mount "$mount" 2>/dev/null
        
        if ! timeout 5 stat "$mount" &>/dev/null; then
            echo "$(date): Remount failed for $mount"
            # Send alert via HA webhook
            curl -s -X POST "http://gateway.home.local:8123/api/webhook/nfs_failure" \
                 -H "Content-Type: application/json" \
                 -d "{\"mount\": \"$mount\"}"
        fi
    fi
done
```

---

## Firewall Rules

| From | To | Port | Purpose |
|------|-----|------|---------|
| Agent Node | Paperless-NGX | 8000 | Document API for RAG |
| All nodes | NFS | 2049 | File shares |
| All nodes | SMB | 445 | Windows/Mac file access |
| Paperless-AI | Agent (LiteLLM) | 4000 | AI tagging inference |
