# Install at: servers/gateway/caddy/Dockerfile
# Builds Caddy with the Route53 DNS module for automatic
# DNS-01 cert issuance (no port-forwarding needed).

FROM caddy:2-builder-alpine AS builder

RUN xcaddy build \
    --with github.com/caddy-dns/route53

FROM caddy:2-alpine

COPY --from=builder /usr/bin/caddy /usr/bin/caddy
