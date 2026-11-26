#!/usr/bin/env bash
# One-time Azure setup for GitHub Actions OIDC.
# Run in Azure Cloud Shell (az is preinstalled there) or anywhere you are logged in.
#
#   REPO=avinasshkotta/insurance-claims-analytics ./setup-oidc.sh
#
# Creates: resource group, an Entra app + service principal, a Contributor role
# assignment scoped to that group, and a federated credential trusting this repo's main branch.
# No client secret is created, so there is nothing to rotate.
set -euo pipefail

REPO="${REPO:-avinasshkotta/insurance-claims-analytics}"
RG="${RG:-rg-claims-demo}"
LOCATION="${LOCATION:-centralindia}"
APP_NAME="${APP_NAME:-claims-console-ci}"

SUB="$(az account show --query id -o tsv)"
TENANT="$(az account show --query tenantId -o tsv)"

echo "Subscription : $SUB"
echo "Tenant       : $TENANT"
echo "Repo         : $REPO"
echo "Resource grp : $RG ($LOCATION)"
echo

echo "==> resource group"
az group create -n "$RG" -l "$LOCATION" -o none

echo "==> app registration + service principal"
APP_ID="$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)"
az ad sp create --id "$APP_ID" -o none

echo "==> Contributor on the resource group only"
az role assignment create \
  --assignee "$APP_ID" \
  --role Contributor \
  --scope "/subscriptions/$SUB/resourceGroups/$RG" \
  -o none

echo "==> federated credential for $REPO refs/heads/main"
az ad app federated-credential create \
  --id "$APP_ID" \
  --parameters "{
    \"name\": \"github-main\",
    \"issuer\": \"https://token.actions.githubusercontent.com\",
    \"subject\": \"repo:${REPO}:ref:refs/heads/main\",
    \"audiences\": [\"api://AzureADTokenExchange\"]
  }" -o none

cat <<EOF

Add these three repository secrets (Settings -> Secrets and variables -> Actions):

  AZURE_CLIENT_ID       = $APP_ID
  AZURE_TENANT_ID       = $TENANT
  AZURE_SUBSCRIPTION_ID = $SUB

Then run ./deploy-infra.sh to create the environment and the Container App.
EOF
