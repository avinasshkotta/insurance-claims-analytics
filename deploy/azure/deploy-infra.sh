#!/usr/bin/env bash
# One-time infrastructure deploy. Run after setup-oidc.sh.
#
#   OPENAI_API_KEY=sk-... ./deploy-infra.sh
#
# After this, GitHub Actions owns image updates; do not re-run unless you are
# changing infrastructure (region, scale, env vars, secrets).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RG="${RG:-rg-claims-demo}"
LOCATION="${LOCATION:-centralindia}"
APP_NAME="${APP_NAME:-claims-console}"
IMAGE="${IMAGE:-mcr.microsoft.com/azuredocs/containerapps-helloworld:latest}"

if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "OPENAI_API_KEY is required." >&2
  exit 1
fi

echo "==> checking Microsoft.App availability in $LOCATION"
az provider register -n Microsoft.App -o none || true
LOCATIONS="$(az provider show -n Microsoft.App \
  --query "resourceTypes[?resourceType=='managedEnvironments'].locations" -o tsv || true)"
if [ -n "$LOCATIONS" ] && ! echo "$LOCATIONS" | tr '[:upper:]' '[:lower:]' | grep -qw "$(echo "$LOCATION" | tr '[:upper:]' '[:lower:]')"; then
  echo "Container Apps is not available in $LOCATION. Available:" >&2
  echo "$LOCATIONS" >&2
  exit 1
fi

echo "==> deploying $APP_NAME to $RG ($LOCATION)"
az deployment group create \
  --resource-group "$RG" \
  --name claims-infra \
  --template-file "$ROOT/deploy/azure/main.bicep" \
  --parameters \
      appName="$APP_NAME" \
      location="$LOCATION" \
      image="$IMAGE" \
      openaiApiKey="$OPENAI_API_KEY" \
  -o none

URL="$(az deployment group show -g "$RG" -n claims-infra --query properties.outputs.url.value -o tsv)"
echo
echo "Container App created: $URL"
echo "It will report unhealthy until the first GitHub Actions run replaces the placeholder image."
echo "Push to main (or run the deploy-azure workflow), then:"
echo "  curl -s $URL/api/health"
