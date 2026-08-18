# Server Spec: Storage Node (Synology NAS)

**Hardware:** Synology DiskStation, 16 TB usable storage
**Role:** Storage and backup target only — no application containers run here
**Always-on:** Yes
**IP:** 192.168.13.12

---

## What Runs Here

Nothing. The NAS is pure storage. Paperless-NGX and all associated services
(postgres, redis, paperless-ai) run on the **Agent Node** (192.168.13.22).

The NAS provides NFS exports that the agent node mounts for document storage.

---

## NFS Exports

Configure in Synology DSM: **Control Panel > Shared Folder > Edit > NFS Permissions**

| Shared Folder | Mount Point (client) | Access | Consumers |
|--------------|----------------------|--------|-----------|
| /volume1/paperless/media | /mnt/nas/paperless/media | ro | Agent (RAG pipeline) |
| /volume1/paperless/consume | /mnt/nas/paperless/consume | rw | Agent (drop folder) |
| /volume1/paperless/export | /mnt/nas/paperless/export | ro | Agent (exports) |
| /volume1/backups | /mnt/nas/backups | rw | All nodes |
| /volume1/models | /mnt/nas/models | ro | Agent, Inference (archive only — copy to local NVMe before use) |

**NFS permission rule for each share:** Hostname = `192.168.13.0/24`, Squash = No mapping

Run `scripts/setup-nfs-mounts.sh` on agent and inference nodes to add fstab entries and mount.

---

## Critical Rules

- **NEVER** mount database directories (Qdrant, PostgreSQL, SQLite) over NFS — use local NVMe only
- **NEVER** use `hard` mount option — processes hang indefinitely on NAS failure
- Model weights must be **copied to local NVMe** for inference, not served over NFS

---

## Backup Targets

The NAS is the primary backup destination:

| Source | Method | Schedule | NAS Path | Retention |
|--------|--------|----------|----------|-----------|
| Qdrant snapshots | API + rsync | Daily 2am | /backups/qdrant/ | 30 days |
| LangGraph SQLite | .backup + rsync | Hourly | /backups/langgraph/ | 7 days |
| HA config | git bundle + rsync | Daily 3am | /backups/homeassistant/ | 90 days |
| Paperless DB | pg_dump | Daily 1am | /backups/paperless-db/ | 30 days |
| Grafana data | rsync | Daily 3am | /backups/grafana/ | 30 days |
| Authentik DB | pg_dump | Daily 1am | /backups/authentik-db/ | 30 days |
| VM snapshots | Proxmox Backup Server | Weekly Sun 1am | /backups/pbs/ | 4 weeks |

### Offsite

Synology Hyper Backup or Restic → Backblaze B2 ($0.006/GB/month, ~$2–5/month for critical data).
