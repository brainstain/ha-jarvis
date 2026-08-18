#!/bin/bash
# ============================================================
# NFS mount setup for agent and inference nodes
# Run on each node as root/sudo after NAS NFS exports are
# configured in Synology DSM.
#
# Synology DSM setup (one-time, in the UI):
#   Control Panel > Shared Folder > <folder> > Edit > NFS Permissions
#   Add rule: Hostname/IP = 192.168.13.0/24, Privilege = Read/Write
#   (or Read Only for media/models), Squash = No mapping
# ============================================================
set -euo pipefail

NAS="192.168.13.12"

# Detect which node we're on
NODE_IP=$(hostname -I | awk '{print $1}')

# ── Create mount points ──────────────────────────────────────
mkdir -p /mnt/nas/paperless/media
mkdir -p /mnt/nas/paperless/consume
mkdir -p /mnt/nas/paperless/export
mkdir -p /mnt/nas/backups
mkdir -p /mnt/nas/models

# ── Write fstab entries (idempotent — skip if already present) ──
FSTAB=/etc/fstab

add_fstab() {
    local entry="$1"
    if ! grep -qF "$entry" "$FSTAB"; then
        echo "$entry" >> "$FSTAB"
        echo "Added: $entry"
    else
        echo "Already present: $entry"
    fi
}

# paperless/media — read-only (agent only; inference doesn't need it)
if [[ "$NODE_IP" == "192.168.13.22" ]]; then
    add_fstab "${NAS}:/volume1/paperless/media   /mnt/nas/paperless/media   nfs  soft,intr,timeo=10,ro,_netdev  0  0"
    add_fstab "${NAS}:/volume1/paperless/consume  /mnt/nas/paperless/consume  nfs  soft,intr,timeo=10,rw,_netdev  0  0"
    add_fstab "${NAS}:/volume1/paperless/export   /mnt/nas/paperless/export   nfs  soft,intr,timeo=10,ro,_netdev  0  0"
fi

# backups — read-write for all nodes
add_fstab "${NAS}:/volume1/backups    /mnt/nas/backups    nfs  soft,intr,timeo=10,rw,_netdev  0  0"

# models — read-only (copy to local NVMe before inference, never serve over NFS)
add_fstab "${NAS}:/volume1/models     /mnt/nas/models     nfs  soft,intr,timeo=10,ro,_netdev  0  0"

# ── Mount all ────────────────────────────────────────────────
echo "Mounting all NFS shares..."
mount -a

echo "Done. Current NAS mounts:"
mount | grep "$NAS"
