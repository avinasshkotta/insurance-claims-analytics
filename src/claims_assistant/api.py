"""HTTP API for the claims console (Layer 3/4 over REST).

Thin wrapper: every endpoint delegates to the same service functions the CLI and
MCP server use (toolkit.svc_*, agent.run_agent, evaluate.run_eval) — no
duplicated business logic.
"""
from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import compliance, config, demo, guardrails, risk, severity, usage
from .agent import run_agent
from .embeddings import EmbeddingIndex
from .evaluate import run_eval
from .ingest import ingest_document
from .llm import LLMClient
from .prompts import registry
from .rag import PolicyRAG
from .services import CaseQueue, ClaimsDB
from .toolkit import build_direct_handlers, svc_answer
from .tracing import list_runs, record_run

MAX_UPLOAD_BYTES = 5 * 1024 * 1024

app = FastAPI(title="Claims Triage Console", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

db = ClaimsDB()
queue = CaseQueue()
_llm: LLMClient | None = None


@app.middleware("http")
async def demo_guard(request: Request, call_next):
    """Public-demo guards: per-IP rate limit, spend cap, uploads off.

    Defined before any route so it also covers the mounted frontend; the guard
    itself no-ops when DEMO_MODE is off.
    """
    try:
        demo.check_request(request)
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=exc.headers or {},
        )
    return await call_next(request)


def get_llm() -> LLMClient:
    global _llm
    if not config.OPENAI_API_KEY:
        raise HTTPException(status_code=503,
                            detail="OPENAI_API_KEY not set in .env")
    if _llm is None:
        _llm = LLMClient()
    return _llm


def _risk_brief(claim_id: str) -> dict:
    scored = risk.score_claim(db.get(claim_id))
    return {
        "score": scored["score"],
        "flagged": scored["score"] >= config.RISK_THRESHOLD,
        "borderline": compliance.borderline(scored),
        "signals": [{"signal": m["signal"], "weight": m["weight"],
                     "description": m["description"], "evidence": m["evidence"]}
                    for m in scored["matched"]],
    }


def _severity_brief(claim_id: str) -> dict | None:
    try:
        return severity.predict_claim(db.get(claim_id))
    except Exception:
        return None


# --------------------------------------------------------------------------
# Read endpoints (no LLM — fast)
# --------------------------------------------------------------------------
@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "model": config.LLM_MODEL,
        "threshold": config.RISK_THRESHOLD,
        "prompts": registry(),
        "has_api_key": bool(config.OPENAI_API_KEY),
        "retrieval": "hybrid" if config.EMBEDDINGS_PATH.exists() else "bm25",
        "demo": demo.status(),
    }


@app.get("/api/claims")
def claims() -> dict:
    out = []
    for c in db.list():
        out.append({
            "claim_id": c["claim_id"],
            "claim_type": c["claim_type"],
            "region": c["region"],
            "status": c["status"],
            "claimed_amount": c.get("claimed_amount"),
            "claimant": c.get("claimant"),
            "channel": c.get("channel"),
            "incident_date": c.get("incident_date"),
            "reported_date": c.get("reported_date"),
            "risk": _risk_brief(c["claim_id"]),
            "expected_cost": _severity_brief(c["claim_id"]),
            "source": c.get("source"),
            "duplicate_of": c.get("duplicate_of"),
        })
    return {"claims": out, "threshold": config.RISK_THRESHOLD}


@app.get("/api/claims/{claim_id}")
def claim_detail(claim_id: str) -> dict:
    try:
        c = db.get(claim_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown claim {claim_id}")
    return {**c, "risk": _risk_brief(claim_id)}


@app.get("/api/queue")
def get_queue() -> dict:
    return {"entries": queue.list(), "stats": queue.stats()}


class DecisionBody(BaseModel):
    decision: str            # accepted | overridden
    reason: str | None = None


@app.post("/api/queue/{claim_id}/decision")
def record_decision(claim_id: str, body: DecisionBody) -> dict:
    try:
        entry = queue.record_decision(claim_id, body.decision, body.reason)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"claim not in queue: {claim_id}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"entry": entry, "stats": queue.stats()}


@app.post("/api/queue/reset")
def reset_queue() -> dict:
    if queue.path.exists():
        queue.path.unlink()
    queue.entries = []
    return {"ok": True}


@app.get("/api/usage")
def get_usage() -> dict:
    return usage.summary()


@app.get("/api/eval")
def get_eval() -> dict:
    return run_eval(write_report=False)


@app.get("/api/runs")
def get_runs(limit: int = 20) -> dict:
    return {"runs": list_runs(limit=limit)}


@app.get("/api/severity/{claim_id}")
def get_severity(claim_id: str) -> dict:
    try:
        claim = db.get(claim_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown claim {claim_id}")
    pred = severity.predict_claim(claim)
    if pred is None:
        raise HTTPException(status_code=503,
                            detail="severity model not trained — run "
                                   "`python -m claims_assistant.cli ml`")
    return {"claim_id": claim_id, **pred}


@app.get("/api/ml")
def get_ml() -> dict:
    from .drift import drift_report
    from .fairness import segment_report
    try:
        return {"report": severity.report(),
                "drift": drift_report(),
                "fairness": segment_report()}
    except FileNotFoundError:
        raise HTTPException(status_code=503,
                            detail="model artifacts missing — run "
                                   "`python -m claims_assistant.cli ml`")


@app.post("/api/reindex")
def reindex() -> dict:
    llm = get_llm()
    rag = PolicyRAG(llm=llm)
    index = EmbeddingIndex()
    added = index.build(rag.all_chunks(), llm.embed)
    return {"embedded": added, "total": len(index), "model": index.model}


# --------------------------------------------------------------------------
# Action endpoints (LLM — slower)
# --------------------------------------------------------------------------
class AskBody(BaseModel):
    claim_id: str
    question: str


@app.post("/api/ask")
def ask(body: AskBody) -> dict:
    llm = get_llm()
    scan = guardrails.scan_input(body.question)
    if scan["blocked"]:
        raise HTTPException(status_code=400,
                            detail="input rejected by guardrails: "
                                   + ", ".join(scan["matches"]))
    try:
        db.get(body.claim_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown claim {body.claim_id}")
    rag = PolicyRAG(llm=llm)
    return svc_answer(llm, rag, db, body.claim_id, body.question)


@app.post("/api/triage/{claim_id}")
def triage_one(claim_id: str) -> dict:
    started = time.perf_counter()
    llm = get_llm()
    try:
        db.get(claim_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown claim {claim_id}")
    handlers = build_direct_handlers(llm, db, PolicyRAG(llm=llm), queue)
    result = asyncio.run(run_agent([claim_id], None, llm, handlers=handlers))
    entry = next((e for e in queue.list() if e["claim_id"] == claim_id), None)
    run = record_run("api_triage", [claim_id], result["tool_log"],
                     result["final"], model=llm.model,
                     latency_ms=(time.perf_counter() - started) * 1000)
    return {
        "claim_id": claim_id,
        "run_id": run["run_id"],
        "final": result["final"],
        "tool_log": result["tool_log"],
        "risk": _risk_brief(claim_id),
        "queue_entry": entry,
        "usage": usage.summary(),
    }


# --------------------------------------------------------------------------
# Document ingestion: upload PDF/DOCX/XLSX/CSV/TXT → structured claim
# --------------------------------------------------------------------------
@app.post("/api/uploads")
async def upload_document(file: UploadFile = File(...)) -> dict:
    llm = get_llm()
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename required")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file exceeds 5 MB")
    try:
        result = ingest_document(file.filename, data, llm, db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    claim_id = result["claim"]["claim_id"]
    result["claim"] = {
        **result["claim"],
        "risk": _risk_brief(claim_id),
        "expected_cost": _severity_brief(claim_id),
    }
    return result


# --------------------------------------------------------------------------
# Quality / cost dashboard (static HTML + one JSON endpoint)
# --------------------------------------------------------------------------
def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(pct / 100 * len(ordered)) - 1))
    return round(float(ordered[index]), 2)


# The dashboard is public, and /api/metrics/summary runs the golden set. Cache
# it: a demo visitor must not be able to trigger 100 evaluations a minute.
_METRICS_TTL_SECONDS = 60.0
_metrics_cache: dict = {"at": 0.0, "payload": None}


@app.get("/api/metrics/summary")
def metrics_summary() -> dict:
    """Everything the dashboard shows, in one call.

    Deliberately computed from the artifacts the app already writes:
    the golden-set eval, the token ledger, run traces and the review queue.
    No separate metrics store, nothing to keep in sync.
    """
    now = time.time()
    cached = _metrics_cache["payload"]
    if cached is not None and now - _metrics_cache["at"] < _METRICS_TTL_SECONDS:
        return cached

    try:
        evaluation = run_eval(write_report=False)
        eval_error = None
    except Exception as exc:  # dashboard must render even if eval breaks
        evaluation, eval_error = None, str(exc)

    u = usage.summary()
    runs = list_runs(limit=500)
    latencies = [
        float(r["latency_ms"]) for r in runs
        if isinstance(r.get("latency_ms"), (int, float))
    ]
    stats = queue.stats()
    calls = int(u.get("calls", 0))

    headline = {
        "golden_set_pass_rate": evaluation["metrics"]["pass_rate"] if evaluation else None,
        "golden_set_passed": evaluation["passed"] if evaluation else None,
        "cost_per_request_usd": round(u["cost_usd"] / calls, 6) if calls else 0.0,
        "total_cost_usd": round(u["cost_usd"], 6),
        "p95_latency_ms": _percentile(latencies, 95),
        "median_latency_ms": _percentile(latencies, 50),
        "override_rate": stats["override_rate"],
        "pending_reviews": stats["pending"],
        "runs_measured": len(latencies),
    }
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "headline": headline,
        "eval": None if evaluation is None else {
            "mode": evaluation["mode"],
            "model": evaluation.get("model"),
            "cases": evaluation["metrics"]["cases"],
            "metrics": evaluation["metrics"],
            "thresholds": evaluation["thresholds"],
            "passed": evaluation["passed"],
        },
        "usage": u,
        "queue": stats,
        "demo": demo.status(),
        "error": eval_error,
    }
    _metrics_cache.update({"at": now, "payload": payload})
    return payload


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    return DASHBOARD_HTML


DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Claims Triage Console - quality &amp; cost</title>
<style>
  :root { --bg:#0b1020; --card:#141a2e; --line:#26304b; --ink:#e8ecf6; --muted:#93a0bd;
          --ok:#3ddc97; --bad:#ff6b6b; --accent:#7aa2ff; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:15px/1.5 ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
  header { padding:28px 24px 8px; max-width:1100px; margin:0 auto; }
  h1 { margin:0 0 6px; font-size:22px; letter-spacing:-.02em; }
  .sub { color:var(--muted); font-size:13px; }
  .banner { margin:14px auto 0; max-width:1100px; padding:10px 14px; border-radius:10px;
            background:#2a2135; border:1px solid #4a3a60; color:#e8d9ff; font-size:13px; display:none; }
  main { max-width:1100px; margin:0 auto; padding:18px 24px 60px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px; }
  .label { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.06em; }
  .value { font-size:26px; font-weight:600; margin-top:6px; letter-spacing:-.02em; }
  .value.small { font-size:18px; }
  table { width:100%; border-collapse:collapse; margin-top:10px; font-size:14px; }
  th, td { text-align:left; padding:9px 10px; border-bottom:1px solid var(--line); }
  th { color:var(--muted); font-weight:500; font-size:12px; text-transform:uppercase; letter-spacing:.05em; }
  .pass { color:var(--ok); } .fail { color:var(--bad); }
  .row { display:flex; gap:12px; flex-wrap:wrap; margin-top:22px; }
  .row > section { flex:1 1 420px; background:var(--card); border:1px solid var(--line);
                   border-radius:12px; padding:16px 18px; }
  h2 { font-size:14px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:0 0 4px; }
  footer { color:var(--muted); font-size:12px; margin-top:26px; }
  a { color:var(--accent); }
  .err { color:var(--bad); }
</style>
</head>
<body>
<header>
  <h1>Claims Triage Console &mdash; quality &amp; cost</h1>
  <div class="sub">Live numbers from the golden-set eval, token ledger, run traces and review queue.</div>
  <div class="banner" id="banner"></div>
</header>
<main>
  <div class="grid">
    <div class="card"><div class="label">Golden-set pass rate</div><div class="value" id="pass">-</div></div>
    <div class="card"><div class="label">p95 latency</div><div class="value" id="p95">-</div></div>
    <div class="card"><div class="label">Cost per request</div><div class="value small" id="cpr">-</div></div>
    <div class="card"><div class="label">Total spend</div><div class="value small" id="spend">-</div></div>
    <div class="card"><div class="label">Human override rate</div><div class="value" id="override">-</div></div>
    <div class="card"><div class="label">Pending reviews</div><div class="value" id="pending">-</div></div>
  </div>

  <div class="row">
    <section>
      <h2>Retrieval &amp; refusal checks</h2>
      <table>
        <thead><tr><th>Metric</th><th>Actual</th><th>Floor</th><th>Status</th></tr></thead>
        <tbody id="eval"></tbody>
      </table>
    </section>
    <section>
      <h2>What this page proves</h2>
      <p class="sub" style="margin:8px 0 0">
        The pass rate comes from a fixed golden set of policy questions that must pass
        before any prompt or retrieval change ships. Cost comes from per-call token
        accounting. Override rate is how often a human disagreed with the agent's triage.
      </p>
      <p class="sub" style="margin:10px 0 0" id="meta"></p>
    </section>
  </div>

  <footer>
    Synthetic claims only &mdash; no real customer data. The agent routes claims to a human
    adjuster; it never approves, denies or settles anything.
    &nbsp;|&nbsp; <a href="/">Open the console</a>
  </footer>
</main>
<script>
const pct = v => (v === null || v === undefined) ? '-' : (v * 100).toFixed(0) + '%';
const money = (v, d=4) => (v === null || v === undefined) ? '-' : '$' + Number(v).toFixed(d);
const ms = v => (v === null || v === undefined) ? '-' : Math.round(v) + ' ms';

function row(name, actual, floor) {
  const ok = floor === null || floor === undefined || actual >= floor;
  return `<tr><td>${name}</td><td>${pct(actual)}</td><td>${pct(floor)}</td>
    <td class="${ok ? 'pass' : 'fail'}">${ok ? 'pass' : 'below'}</td></tr>`;
}

async function load() {
  try {
    const res = await fetch('/api/metrics/summary');
    const d = await res.json();
    const h = d.headline || {};
    document.getElementById('pass').textContent = pct(h.golden_set_pass_rate);
    document.getElementById('p95').textContent = ms(h.p95_latency_ms);
    document.getElementById('cpr').textContent = money(h.cost_per_request_usd, 5);
    document.getElementById('spend').textContent = money(h.total_cost_usd, 3);
    document.getElementById('override').textContent = pct(h.override_rate);
    document.getElementById('pending').textContent = h.pending_reviews ?? '-';

    if (d.eval) {
      const m = d.eval.metrics, t = d.eval.thresholds;
      document.getElementById('eval').innerHTML =
        row('Pass rate', m.pass_rate, t.pass_rate) +
        row('Top-1 retrieval', m.top1_accuracy, t.top1_accuracy) +
        row('Retrieval hit rate', m.retrieval_hit_rate, t.retrieval_hit_rate) +
        row('Refusal accuracy', m.refusal_accuracy, t.refusal_accuracy) +
        row('Keyword coverage', m.keyword_coverage, t.keyword_coverage);
      document.getElementById('meta').textContent =
        `${d.eval.cases} cases, ${d.eval.mode} mode, ${d.eval.passed ? 'gate clear' : 'gate failing'}.` +
        ` Median latency ${ms(h.median_latency_ms)} over ${h.runs_measured} measured runs.`;
    }
    if (d.demo && d.demo.enabled) {
      const b = document.getElementById('banner');
      b.style.display = 'block';
      b.textContent = `Public demo: ${d.demo.rpm_per_ip} requests/min per visitor, ` +
        `${money(d.demo.remaining_usd, 2)} of ${money(d.demo.daily_usd_cap, 2)} budget left today. ` +
        `Uploads are ${d.demo.uploads_enabled ? 'enabled' : 'disabled'}.`;
    }
    if (d.error) {
      document.getElementById('meta').innerHTML =
        `<span class="err">eval error: ${d.error}</span>`;
    }
  } catch (e) {
    document.getElementById('meta').innerHTML =
      `<span class="err">could not load metrics: ${e}</span>`;
  }
}
load();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# Serve the built frontend (Docker / production); Vite dev server proxies /api.
# --------------------------------------------------------------------------
_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="frontend")
