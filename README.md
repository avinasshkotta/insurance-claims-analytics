# Insurance Claims Analytics — Four-Layer Build-Out

The Insurance Claims capstone run through all four solution layers in order, shipped as a
tested Python service with a React console, an evaluation harness, and a Docker image.

| Layer | What it adds | Module |
|---|---|---|
| 1 · LLM | Plain-language claim verbatim summaries (PII-redacted before the prompt) | `llm.py` |
| 2 · RAG | Policy answers grounded in the retrieved regional document — or refused | `rag.py` |
| 3 · AI Agent | Triage: status → deterministic risk → route flagged claims to a human | `agent.py` + `risk.py` |
| 4 · MCP | Three least-privilege MCP servers behind one client interface | `mcp_*_server.py` |

**Live demo:** https://claims-console.proudgrass-320c667e.centralindia.azurecontainerapps.io
**Quality & cost dashboard:** https://claims-console.proudgrass-320c667e.centralindia.azurecontainerapps.io/dashboard

Deployed on Azure Container Apps via GitHub Actions; every push to `main` rebuilds and rolls a new revision.
Cold start is ~5–10 s (scale-to-zero).

## Measured results

Measured against the live model (`gpt-4o-mini`) over 24 cases: the original 9-case golden
set plus a 15-case **hard set** (`eval/hard_set.json`) built around paraphrases, two-sided
answers, numeric boundaries and multi-part questions.

| Metric | Before | After | Source |
|---|---|---|---|
| Hard-set pass rate (15 cases) | **86.7%** | **100%** | `eval/hard_set.json` |
| Top-1 retrieval accuracy | 100% | 100% | both sets |
| Refusal accuracy | 93.3% | 100% | both sets |
| Keyword coverage | 88.9% | 100% | both sets |
| Golden-set pass rate (9 cases) | 100% | 100% | `eval/golden_set.json` (saturated) |
| Cost per model call | — | **$0.0001** | `data/llm_usage.jsonl` (518 calls, $0.055 total) |
| p95 end-to-end latency | — | — | needs triage runs; not yet measured |
| External sessions | — | 0 | demo guard logs |

**What changed:** the hard set exposed two retrieval failures and one prompt gap.

1. *"My basement flooded"* retrieved nothing — `flooded` and `floodwaters` share no token.
   Added a small stemmer + domain synonyms applied to both the index and the query.
2. *"Do I need prior authorization for an MRI, and how long do I have to submit the claim?"*
   answered only the first half — the Filing Deadline section was never retrieved. Added
   multi-query retrieval, so each part of a compound question retrieves its own section.
3. Flood questions were then refused because coverage is conditional. Added a prompt rule
   to answer both branches (covered / excluded) instead of refusing.

The original golden set stayed at 100% throughout — the fixes did not regress it.

> Numbers come from synthetic claim data. The pipeline is the deliverable, not the labels.

The specification, process report and interview pack live outside this repository, so
presentation artifacts never ship with the code.

## Stack

Python 3.10+ · OpenAI-compatible chat model (function calling) · hosted embeddings +
local vector cache (BM25 fallback) · document ingestion with vision OCR (PDF/DOCX/XLSX/CSV/PNG) · MCP SDK ·
FastAPI · React 18 + Vite 5 · Docker

## Public demo mode

A public link runs on your API key, so `DEMO_MODE=1` turns on three guardrails:

| Variable | Default | Effect |
|---|---|---|
| `DEMO_MODE` | `0` | Enables all guards below |
| `DEMO_RPM` | `12` | Requests per minute per visitor (429 + `Retry-After`) |
| `DEMO_DAILY_USD` | `2.00` | Hard spend cap from the token ledger; blocks paid calls |
| `DEMO_ALLOW_UPLOADS` | `0` | Uploads return 403 (keeps PII out of a public demo) |
| `MAX_OUTPUT_TOKENS` | `700` | Generation ceiling on every call, all modes |

`GET /api/health` reports the live demo state; `/dashboard` shows quality, latency and spend.
Deployment options: **Azure Container Apps + GitHub Actions** (`deploy/azure/`, CI/CD on push),
**Render free tier** (`render.yaml`), **local Docker + DuckDNS** (`deploy/local-demo/`),
**AWS Lambda** (`deploy/aws/`), **Cloud Run** (`deploy/cloudrun/`), or **Hugging Face Spaces**.

## Quick start

```powershell
cd Insurance-claims-4layer
pip install -r requirements.txt
pip install -e .
Copy-Item .env.example .env     # paste your OPENAI_API_KEY
```

Run the API (port **8010** — 8000 is often taken on this machine):

```powershell
python -m uvicorn claims_assistant.api:app --host 127.0.0.1 --port 8010
```

Run the console (second terminal):

```powershell
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173** — triage claims, inspect risk reasons, ask policy questions,
and watch the adjuster queue fill. (`npm.ps1` may be blocked by your PowerShell policy;
if so use `npm.cmd install` / `npm.cmd run dev`.)

Production build (single origin, UI served by FastAPI):

```powershell
cd frontend && npm run build
cd ..
python -m uvicorn claims_assistant.api:app --port 8010
# → http://localhost:8010 serves the built UI + API
```

## CLI

```powershell
python -m pytest -q                                  # 101 tests, no key needed
python -m claims_assistant.cli eval                  # golden-set regression gate (offline)
python -m claims_assistant.cli eval --live           # same, against real model answers
python -m claims_assistant.cli eval --dataset eval/hard_set.json --live
                                                    # 15 harder cases: paraphrases,
                                                    # two-sided answers, multi-part
python -m claims_assistant.cli reindex               # build the semantic index (embeddings)
python -m claims_assistant.cli ml                    # train severity model + clusters (synthetic history)
python -m claims_assistant.cli drift                 # PSI drift report vs training baseline
python -m claims_assistant.cli fairness              # segment parity audit
python -m claims_assistant.cli triage                # triage all open/under_review claims
python -m claims_assistant.cli triage --via-mcp      # same run through the MCP server
python -m claims_assistant.cli ask CLM-1042 "What is the daily rental reimbursement limit?"
```

## Document ingestion (unstructured → structured claim)

Upload a **PDF, DOCX, XLSX, CSV, TXT, MD or image (PNG/JPG)** in the console (or
`POST /api/uploads`). Text files are parsed directly; scanned PDFs and images are rendered and
read with **vision OCR**. An extraction agent returns claim JSON, and a deterministic validator
checks types, regions, ISO dates, positive amounts and verbatim length, feeding failures back
for repair (max 3 attempts). Every attempt is traced; if validation still fails, the claim is
saved as **`needs_review`** rather than guessed. Duplicate detection (same type+region, amount
within 5%, incident date within 7 days, matching claimant) also holds a claim for review with
`duplicate_of`.

```powershell
curl.exe -F "file=@samples/home-claim-intake.pdf" http://127.0.0.1:8010/api/uploads
```

Sample documents: `samples/home-claim-intake.pdf`, `samples/auto-claim-intake.docx`,
`samples/scanned-claim.pdf` (vision OCR), `samples/claim-photo.png` (vision OCR),
`samples/health-claim-intake.csv`, `samples/claims.xlsx`. See `samples/README.md` for the
expected outcome of each file. Some samples intentionally duplicate existing claims to
demonstrate deduplication.

## Documentation and walkthrough

- **Executed notebook:** `notebooks/insurance_claims_walkthrough.ipynb` — 24 cells covering
  data, RAG, risk, compliance, ingestion/OCR, ML, evaluation, guardrails and tests.
- **Presentation documents** — project guide, process report, interview Q&A and interview pack —
  are kept **outside the repository** in `..\Insurance-Claims-Docs\` (see its README). They are
  intentionally not committed.

The repo ignores `docs/*.pdf`, `docs/*.html` and `docs/*.png` so presentation artifacts can never
be committed by accident.

## Docker

```powershell
docker build -t claims-console .
docker run -d --name claims-console -p 8020:8000 --env-file .env claims-console
# → http://localhost:8020 (UI + API)
```

The image builds the Vite bundle in a Node stage, installs the Python service, and ships the
trained severity model. `scikit-learn` is pinned to **1.7.2** so the saved joblib bundle loads
(the pickle references private sklearn modules; version drift breaks it). Regenerate the model
inside a different environment with `python -m claims_assistant.cli ml`.

## Project layout

```
├── Dockerfile                  # multi-stage: Vite build → FastAPI runtime
├── prompts/                    # versioned prompts (summary, rag, triage, ingestion)
├── eval/golden_set.json        # 9 policy Q&A cases + thresholds
├── samples/                    # sample intake documents (text + scanned + image)
├── notebooks/                  # executed walkthrough notebook
├── docs/                       # guide, report, Q&A (PDFs git-ignored)
├── scripts/make_samples.py     # regenerates the sample documents
├── models/                     # trained severity bundle + drift baseline (generated)
├── data/                       # claims, regional policies, case queue (runtime)
├── src/claims_assistant/
│   ├── llm.py                  # Layer 1 — summaries, PII redaction, metering
│   ├── rag.py                  # Layer 2 — filter-first retrieval (hybrid), refuse-or-answer
│   ├── embeddings.py           # embedding cache + RRF fusion (BM25 fallback)
│   ├── guardrails.py           # injection scan, PII redaction, citation verification
│   ├── risk.py                 # Layer 3 — 12 weighted, explainable signals
│   ├── agent.py                # Layer 3 — tool loop, direct + MCP transports
│   ├── mcp_claims_server.py    # Layer 4 — claims DB server (read-only)
│   ├── mcp_docs_server.py      # Layer 4 — document store server
│   ├── mcp_case_server.py      # Layer 4 — case management server (only writer)
│   ├── mcp_common.py           # shared SDK compat + token boundary
│   ├── toolkit.py              # shared svc_* functions (single source of truth)
│   ├── api.py                  # FastAPI (claims, triage, ask, queue, usage, eval, runs, ml)
│   ├── evaluate.py             # Phase 6 evaluation harness
│   ├── tracing.py              # persisted run traces (data/runs.jsonl)
│   ├── severity.py             # severity regression + KMeans clusters + SHAP
│   ├── ml_data.py              # synthetic claims history (replace with real labels)
│   ├── drift.py                # PSI drift monitoring
│   ├── fairness.py             # segment parity audit
│   ├── compliance.py           # routing gate + borderline notes
│   ├── ingest.py               # document → text → agent extraction → claim JSON
│   ├── prompts.py              # prompt registry (version + content digest)
│   └── usage.py                # token/cost accounting (JSONL)
├── frontend/                   # React + Vite console
└── tests/                      # 110 tests
```

## Design notes

- **Deterministic triage.** Risk scores are weighted rules, never LLM judgment — identical
  inputs route identically, and every point is attributable to a named signal with an
  evidence span. The LLM orchestrates and explains.
- **Refuse, don't guess.** Retrieval filters by claim type + region before ranking; empty
  retrieval returns `REFUSED` without calling the model.
- **Hybrid retrieval.** BM25 + hosted embeddings fused with reciprocal rank fusion when an
  index exists (`cli reindex`); BM25 alone offline, so nothing breaks without a key.
- **Guardrails.** Inputs and retrieved chunks are scanned for instruction hijacking; PII is
  redacted before prompts; answers' citations are verified against the retrieved set.
- **Traces + cost.** Every run is persisted to `data/runs.jsonl` (tools, args, prompt
  versions, outcome); every model call is metered in `data/llm_usage.jsonl` and surfaced in
  the UI and `GET /api/usage`.
- **Same tools, two transports.** Layer 4 is three least-privilege stdio servers —
  claims (read-only), documents, case-management (the only writer) — merged by one client;
  direct and MCP paths call the same `svc_*` functions, and a parity test asserts the union
  matches. Servers refuse to start without `MCP_AUTH_TOKEN`.
- **Model layer.** Gradient-boosted severity regression (MAE ≈ $3.9k vs $12.9k baseline,
  R² 0.84 on synthetic history), KMeans anomaly clusters with severity profiles, and SHAP
  drivers per claim — surfaced as "expected cost" in the console via `/api/severity/{id}`.
  Labels are synthetic; the pipeline is the deliverable.
- **Drift + fairness.** PSI monitoring per feature (`cli drift`, retrain verdict at >0.2) and
  a segment parity audit (`cli fairness`) that also proves protected attributes are excluded
  from model features.
- **Compliance gate + human decisions.** Routing is blocked deterministically when a claim is
  closed/denied, already routed, below threshold, or a signal lacks evidence; scores within
  ±0.10 of the threshold carry a borderline note. Every routing record includes the model's
  expected cost and SHAP drivers. Adjusters record accept/override (with reason) and the
  override rate is surfaced in the UI and `GET /api/queue`.
- **Evaluation gate.** The golden set must pass before shipping — `pytest` runs it offline,
  `cli eval --live` runs it against the model.
- **Prompt provenance + cost.** Prompts live in `prompts/` with declared versions and
  content digests; every LLM call logs tokens and estimated cost to
  `data/llm_usage.jsonl` (surfaced in the UI and `GET /api/usage`).
- **Human-in-the-loop.** The agent cannot approve or deny; it routes to an adjuster with
  score, reasons, and summary attached.
