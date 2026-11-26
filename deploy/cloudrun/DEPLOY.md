# Deploying the public demo (Google Cloud Run) — effectively $0

Cloud Run's monthly free tier covers this demo comfortably:

| Free per month | Enough for |
|---|---|
| 2,000,000 requests | far more than a portfolio demo |
| 180,000 vCPU-seconds | ~50 hours of one 1-vCPU instance |
| 360,000 GiB-seconds | ~100 hours of one 1 GiB instance |
| 1 GB network egress | a few thousand page views |

Because `--min-instances 0`, an idle demo costs **nothing**. The trade-off is a **billing
account is required** (card on file). Create a budget alert at $1–5 **before** the first
deploy; a demo like this normally bills $0.00.

> Behind a proxy, the app's demo guard reads `X-Forwarded-For`, which Cloud Run sets, so
> per-visitor rate limiting works as intended.

## 1. One-time setup

```bash
gcloud auth login
gcloud config set project <your-project-id>
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
    artifactregistry.googleapis.com secretmanager.googleapis.com
```

## 2. Deploy from source (Cloud Run builds the Dockerfile)

```bash
gcloud run deploy claims-console \
  --source . \
  --region asia-south1 \
  --allow-unauthenticated \
  --port 8000 \
  --memory 1Gi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 2 \
  --concurrency 20 \
  --timeout 300 \
  --set-env-vars "DEMO_MODE=1,DEMO_RPM=10,DEMO_DAILY_USD=2,DEMO_ALLOW_UPLOADS=0,MAX_OUTPUT_TOKENS=400,LLM_MODEL=gpt-4o-mini"
```

`--max-instances 2` is the cost circuit-breaker: a traffic spike scales to two containers
and stops, rather than to fifty.

## 3. Add the API key as a secret

```bash
printf '%s' 'sk-...' | gcloud secrets create openai-key --data-file=-

PROJECT_NUMBER=$(gcloud projects describe "$(gcloud config get-value project)" \
  --format='value(projectNumber)')

gcloud secrets add-iam-policy-binding openai-key \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud run services update claims-console \
  --region asia-south1 \
  --set-secrets OPENAI_API_KEY=openai-key:latest
```

Also set a **spend limit on the provider key itself**. The in-app daily cap is a courtesy
to visitors; the provider-side limit is the real protection.

## 4. Verify

```bash
URL=$(gcloud run services describe claims-console --region asia-south1 \
      --format='value(status.url)')

curl -s "$URL/api/health" | jq '.demo, .retrieval'
curl -s "$URL/api/metrics/summary" | jq '.headline'
curl -s -o /dev/null -w "%{http_code}\n" "$URL/dashboard"      # 200
curl -s -o /dev/null -w "%{http_code}\n" -X POST "$URL/api/uploads"   # 403 in demo mode
```

## 5. Cost guardrails

```bash
# Optional: hard cap the service so it cannot scale past two containers
gcloud run services update claims-console --region asia-south1 \
  --max-instances 2

# Budget alert (Console is easier): Billing -> Budgets & alerts -> $1
```

Free-tier numbers and UI details change; check the current
[Cloud Run pricing page](https://cloud.google.com/run/pricing) before you rely on them.

## Alternatives, honestly compared

| Option | Cost | Card needed | Catch |
|---|---|---|---|
| **Cloud Run** | **$0 at demo traffic** | yes | free tier has monthly limits; cold start ~2–5 s |
| Render (free web service) | $0 | no | 512 MB RAM — this image imports pandas/sklearn/shap and may OOM; sleeps after 15 min, ~50 s cold start |
| Hugging Face Docker Space | **PRO ~$9/mo** | yes | Docker/Gradio Spaces now require a paid plan; free CPU hardware is not enough to create one |
| Oracle Cloud Always Free | $0 | yes (verification) | 4 OCPU / 24 GB ARM VM is generous, but you run the VM, TLS and reverse proxy yourself |
| AWS ECS Fargate / App Runner | ~$9–25/mo | yes | no meaningful free tier for containers |

If you want a **no-card** option and can accept a slower first load, Render free works —
but test the memory footprint first. If it OOMs, the fix is to lazy-import the severity
model (`shap`) instead of importing it at API startup.
