#!/usr/bin/env bash
# Point a DuckDNS subdomain at this machine's current public IP.
#   ./duckdns-update.sh
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
[ -f .env ] && set -a && . ./.env && set +a

: "${DOMAIN:?DOMAIN missing (copy .env.example to .env)}"
: "${DUCKDNS_TOKEN:?DUCKDNS_TOKEN missing}"

SUB="${DOMAIN%.duckdns.org}"
PUBLIC_IP="$(curl -fsS https://api.ipify.org || echo unknown)"

RESPONSE="$(curl -fsS "https://www.duckdns.org/update?domains=${SUB}&token=${DUCKDNS_TOKEN}&ip=")"
[ "$RESPONSE" = "OK" ] || { echo "DuckDNS rejected the update: $RESPONSE" >&2; exit 1; }

echo "DuckDNS updated: ${DOMAIN} -> ${PUBLIC_IP}"
