# Deploying the public demo (Hugging Face Spaces)

> **Cost check (2026):** Hugging Face now requires a paid plan (PRO, ~$9/month) to create
> **Gradio or Docker** Spaces. Only *Static* Spaces are free, and a static Space cannot
> run this API. The CPU Basic hardware is still listed at $0/hour, but you cannot create
> a Docker Space on a free account.
>
> If $0 is the requirement, use the Cloud Run guide in [`../cloudrun/DEPLOY.md`](../cloudrun/DEPLOY.md).

Cold start after idle is roughly 1–2 minutes, so open the URL once before you share it.

## 1. Create the Space

1. huggingface.co → **New Space** → SDK: **Docker** → Blank → CPU basic (free).
2. Authenticate locally: `hf auth login` (or `huggingface-cli login`).
3. Push this repo's runtime files plus the Space card:

```powershell
.\deploy\huggingface\push.ps1 -Space <your-user>/claims-console
```

```bash
./deploy/huggingface/push.sh <your-user>/claims-console
```

The script copies only what the image needs, never `.env`, and strips local runtime logs.

## 2. Set the Space variables

Settings → **Variables and secrets**. `OPENAI_API_KEY` must be a **secret**; the rest are
plain variables.

| Name | Value | Why |
|---|---|---|
| `OPENAI_API_KEY` | `sk-...` | secret; use a key with a low monthly limit |
| `OPENAI_BASE_URL` | optional | point at any OpenAI-compatible host (Groq, Gemini, etc.) |
| `LLM_MODEL` | `gpt-4o-mini` | cheapest model that still does tool calls |
| `DEMO_MODE` | `1` | enables all guards |
| `DEMO_RPM` | `10` | per-visitor rate limit |
| `DEMO_DAILY_USD` | `2.00` | hard spend cap |
| `DEMO_ALLOW_UPLOADS` | `0` | keep PII out of the demo |
| `MAX_OUTPUT_TOKENS` | `400` | shorter answers, lower cost |

Set a **spend limit on the API key itself** as well. The app cap is a courtesy to visitors;
the provider-side limit is the real protection if something loops.

## 3. Verify

```bash
curl -s https://<space>.hf.space/api/health | jq '.demo, .retrieval'
curl -s https://<space>.hf.space/api/metrics/summary | jq '.headline'
curl -s -o /dev/null -w "%{http_code}\n" https://<space>.hf.space/dashboard   # 200
```

Expected demo behaviour:

- `POST /api/uploads` → **403** (`disabled in this public demo`)
- more than `DEMO_RPM` paid calls in a minute → **429** with `Retry-After`
- once the ledger reaches `DEMO_DAILY_USD` → **429** (`daily budget`)
- `/dashboard` → 200 and shows the golden-set pass rate

## 4. Troubleshooting

| Symptom | Cause |
|---|---|
| Build succeeds, page never loads | Container not listening on 8000 — `app_port: 8000` in the Space README |
| 503 on every paid call | `OPENAI_API_KEY` missing or set as a variable instead of a secret |
| 403 on `/` | The frontend was not built; the script copies `frontend/` sources, not `dist/` |
| Slow first request | Free CPU cold start; open the URL before sharing it |
| Dashboard shows "eval error" | `eval/golden_set.json` missing from the image |

## 5. What to capture for the resume

Screenshot `/dashboard` after your first week of real sessions, and record one number:
the golden-set pass rate before vs after your fixes. Put the URL and that pair in the
README **Measured results** table.
