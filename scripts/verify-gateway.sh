#!/bin/bash
# ============================================================
# Gateway Node — Deployment Verification
# Run from your control machine (not the Gateway VM itself).
# Install at: scripts/verify-gateway.sh
#
# Usage: ./scripts/verify-gateway.sh
# Override defaults with env vars, e.g.:
#   SSH_USER=michael GATEWAY_IP=192.168.13.29 ./scripts/verify-gateway.sh
# ============================================================
set -uo pipefail

GATEWAY_IP="${GATEWAY_IP:-192.168.13.29}"
GATEWAY_HOST="${GATEWAY_HOST:-gateway.michaelgoldstein.co}"
SSH_USER="${SSH_USER:-michael}"
DEPLOY_DIR="${DEPLOY_DIR:-/home/michael/homelab-ai}"
DOMAIN="${DOMAIN:-michaelgoldstein.co}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
pass() { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; FAILURES=$((FAILURES+1)); }
warn() { echo -e "  ${YELLOW}!${NC} $1"; }

FAILURES=0
ssh_cmd() { ssh -o ConnectTimeout=5 "${SSH_USER}@${GATEWAY_IP}" "$@"; }

echo "═══ 1. Container status ═══"
CONTAINERS="caddy authentik authentik-db authentik-worker redis pihole searxng uptime-kuma nut"
RUNNING=$(ssh_cmd "docker ps --format '{{.Names}}:{{.Status}}'" 2>/dev/null)
if [[ -z "$RUNNING" ]]; then
    fail "Could not reach ${GATEWAY_IP} via SSH, or no containers running"
else
    for c in $CONTAINERS; do
        status=$(echo "$RUNNING" | grep "^${c}:" | cut -d: -f2-)
        if [[ "$status" == Up* ]]; then
            pass "$c — $status"
        else
            fail "$c — not running (${status:-not found})"
        fi
    done
fi
echo ""

echo "═══ 2. Caddy cert issuance (Route53 DNS-01) ═══"
CADDY_LOGS=$(ssh_cmd "docker logs caddy --since 30m 2>&1" 2>/dev/null)
if echo "$CADDY_LOGS" | grep -qi "certificate obtained successfully"; then
    OBTAINED=$(echo "$CADDY_LOGS" | grep -ci "certificate obtained successfully")
    pass "Certificates obtained: $OBTAINED"
else
    warn "No 'certificate obtained' lines found yet — may still be in progress, or already cached from an earlier run"
fi
if echo "$CADDY_LOGS" | grep -qi "error\|failed"; then
    warn "Caddy logs contain error/failed lines — check manually: ssh ${SSH_USER}@${GATEWAY_IP} docker logs caddy"
fi
echo ""

echo "═══ 3. Pi-hole DNS resolution (direct query to ${GATEWAY_IP}) ═══"
SUBDOMAINS="gateway agent inference nas ha authentik search uptime pihole grafana webui prometheus litellm perplexica qdrant paperless paperless-ai"
for sub in $SUBDOMAINS; do
    result=$(dig +short +time=3 +tries=1 "@${GATEWAY_IP}" "${sub}.${DOMAIN}" 2>/dev/null)
    if [[ "$result" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
        pass "${sub}.${DOMAIN} -> $result"
    else
        fail "${sub}.${DOMAIN} did not resolve to an IP (got: ${result:-nothing})"
    fi
done
echo ""

echo "═══ 4. HTTPS + valid cert via Caddy (bypassing system DNS) ═══"
GATEWAY_ROUTES="authentik search uptime pihole"
for sub in $GATEWAY_ROUTES; do
    host="${sub}.${DOMAIN}"
    if curl -sf --max-time 8 --resolve "${host}:443:${GATEWAY_IP}" "https://${host}/" -o /dev/null; then
        pass "$host — HTTPS OK, valid cert"
    else
        fail "$host — request failed (check cert issuance or container health above)"
    fi
done
echo ""

echo "═══ Summary ═══"
if [[ "$FAILURES" -eq 0 ]]; then
    echo -e "${GREEN}All checks passed.${NC} Gateway node looks ready."
else
    echo -e "${RED}${FAILURES} check(s) failed.${NC} See details above."
fi
