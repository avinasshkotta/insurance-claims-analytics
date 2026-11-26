# Azure Container Apps + GitHub Actions

```
push to main ──> GitHub Actions ──> build Dockerfile ──> push ghcr.io/...:sha
                                                          │
                                                          ▼
                          az containerapp update --image  ──> new revision
                                                          │
                                                          ▼
              https://claims-console.<random>.centralindia.azurecontainerapps.io
```

No registry cost (GHCR instead of ACR), no long-lived Azure secret (OIDC), no server to patch,
and scale-to-zero means an idle demo costs nothing.

## What you get

| Item | Value |
|---|---|
| URL | `https://claims-console.<random>.<region>.azurecontainerapps.io` — stable |
| TLS | managed |
| Scale | 0 → 2 replicas |
| Cost at demo traffic | **$0** (Container Apps monthly free grant) |
| Cold start | ~5–10 s |

The Container Apps free grant per subscription is 180,000 vCPU-s, 360,000 GiB-s and 2,000,000
requests per month. At 0.5 vCPU / 1 GiB that is ~100 hours of one replica; a portfolio demo
uses minutes. Log Analytics is the only other charge; set a daily cap if you want certainty.

## One-time setup (Azure Cloud Shell — nothing to install)

Open <https://shell.azure.com> and choose Bash. Upload or paste the two scripts from
`deploy/azure/`, then:

```bash
# 1. OIDC trust + resource group
REPO=avinasshkotta/insurance-claims-analytics ./setup-oidc.sh

# 2. infrastructure (placeholders first; CI replaces the image)
OPENAI_API_KEY=sk-... ./deploy-infra.sh
```

`setup-oidc.sh` prints three values. Add them as **repository secrets**
(Settings → Secrets and variables → Actions):

```
AZURE_CLIENT_ID
AZURE_TENANT_ID
AZURE_SUBSCRIPTION_ID
```

Then make the image public so Container Apps can pull it without credentials:
**GitHub → your profile → Packages → `claims-console` → Settings → Change visibility → Public.**
(If you prefer it private, add a GHCR token as a Container App secret and set
`configuration.registries` in the Bicep.)

## Deploy

Any push to `main` runs `.github/workflows/deploy-azure.yml`:

1. builds the Dockerfile (React + FastAPI) with layer caching,
2. pushes `ghcr.io/<owner>/claims-console:<sha>` and `:latest`,
3. signs in with OIDC (no stored secret),
4. `az containerapp update --image …` → new revision,
5. polls `/api/health` for up to two minutes and fails the workflow if it never comes up.

Manual run: **Actions → deploy-azure → Run workflow**.

## Verify

```bash
FQDN=$(az containerapp show -n claims-console -g rg-claims-demo \
  --query properties.configuration.ingress.fqdn -o tsv)
curl -s "https://$FQDN/api/health" | jq '.demo, .retrieval'
curl -s "https://$FQDN/api/metrics/summary" | jq '.headline'
curl -s -o /dev/null -w "%{http_code}\n" "https://$FQDN/dashboard"          # 200
curl -s -o /dev/null -w "%{http_code}\n" -X POST "https://$FQDN/api/uploads" # 403
```

## Order matters on the first run

The infrastructure deploy uses a placeholder image, so the app is *unhealthy* until the
first Actions run replaces it. That is expected. Sequence:

1. `setup-oidc.sh`
2. add the three secrets
3. `deploy-infra.sh` (placeholder image)
4. push to `main` → the workflow builds and updates the image → health passes

## Troubleshooting

| Symptom | Cause |
|---|---|
| **404 on every path**, smoke test loops `curl: (22) ... 404` | The Container App is **Stopped** (`properties.runningStatus`). Check the activity log for a `containerApps/stop/action`. Start it:<br>`SUB=$(az account show --query id -o tsv)`<br>`az rest --method post --url "https://management.azure.com/subscriptions/$SUB/resourceGroups/rg-claims-demo/providers/Microsoft.App/containerApps/claims-console/start?api-version=2024-03-01"`<br>Note: stopping is pointless — `minReplicas: 0` already costs nothing when idle. The workflow now starts a stopped app automatically. |
| `AADSTS70021` / login fails | federated subject must be exactly `repo:OWNER/REPO:ref:refs/heads/main` |
| `AADSTS700213` and the error shows `repo:owner@12345/repo@67890:ref:refs/heads/main` | GitHub now sends **immutable-ID subject claims**. Add a second credential using the exact subject from the error:<br>`az ad app federated-credential create --id <appId> --parameters '{"name":"github-main-immutable","issuer":"https://token.actions.githubusercontent.com","subject":"<subject from error>","audiences":["api://AzureADTokenExchange"]}'` |
| `ErrImagePull` / `ImagePullBackOff` | GHCR package is private; make it public or add registry credentials |
| Revision deploys but `/api/health` 503 | the app listens on 8000; `targetPort` must be 8000 |
| `AuthorizationFailed` during deploy | service principal needs Contributor on the resource group |
| Region error | Container Apps not available in `centralindia` for your subscription; run the availability check inside `deploy-infra.sh` and pick another region |
| Slow first request | scale-to-zero cold start (~5–10 s); retry |

## Rollback

```bash
az containerapp revision list -n claims-console -g rg-claims-demo -o table
az containerapp update -n claims-console -g rg-claims-demo \
  --image ghcr.io/avinasshkotta/claims-console:<previous-sha>
```

## QuenchGuard

Do not add it to this workflow. It needs its own Container App plus a password gate first
(no auth, no spend cap today). When you are ready, the same pattern works: second app in the
same environment, `minReplicas: 0`, and an auth layer in front.
