"""HTTP API for the claims console (Layer 3/4 over REST).

Thin wrapper: every endpoint delegates to the same service functions the CLI and
MCP server use (toolkit.svc_*, agent.run_agent, evaluate.run_eval) — no
duplicated business logic.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import compliance, config, guardrails, risk, severity, usage
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
    llm = get_llm()
    try:
        db.get(claim_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown claim {claim_id}")
    handlers = build_direct_handlers(llm, db, PolicyRAG(llm=llm), queue)
    result = asyncio.run(run_agent([claim_id], None, llm, handlers=handlers))
    entry = next((e for e in queue.list() if e["claim_id"] == claim_id), None)
    run = record_run("api_triage", [claim_id], result["tool_log"],
                     result["final"], model=llm.model)
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
# Serve the built frontend (Docker / production); Vite dev server proxies /api.
# --------------------------------------------------------------------------
_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="frontend")
