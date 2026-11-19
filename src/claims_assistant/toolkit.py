"""Shared tool implementations — one source of truth for BOTH transports.

The direct agent (Layer 3) and the MCP server (Layer 4) both delegate to the
`svc_*` functions here, so behavior is identical whether a tool is called
in-process or over MCP. This is the Layer 4 constraint: one interface, zero
duplicated logic.
"""
from __future__ import annotations

import json

from . import config, compliance, risk
from .rag import PolicyRAG
from .services import CaseQueue, ClaimsDB

# --------------------------------------------------------------------------
# OpenAI-format tool schemas (single source for the agent loop)
# --------------------------------------------------------------------------
TOOL_SCHEMAS: list[dict] = [
    {"type": "function", "function": {
        "name": "get_claim_status",
        "description": "Check the current status of a claim in the claims database.",
        "parameters": {"type": "object", "properties": {
            "claim_id": {"type": "string", "description": "e.g. CLM-1041"}},
            "required": ["claim_id"]}}},
    {"type": "function", "function": {
        "name": "summarize_claim",
        "description": "Layer 1: plain-language structured summary of the claim verbatim.",
        "parameters": {"type": "object", "properties": {
            "claim_id": {"type": "string"}},
            "required": ["claim_id"]}}},
    {"type": "function", "function": {
        "name": "answer_policy_question",
        "description": "Layer 2: answer a policy question ONLY from retrieved policy "
                       "documents for this claim's type and region; refuses if nothing "
                       "is retrieved.",
        "parameters": {"type": "object", "properties": {
            "claim_id": {"type": "string"},
            "question": {"type": "string"}},
            "required": ["claim_id", "question"]}}},
    {"type": "function", "function": {
        "name": "assess_review_risk",
        "description": "Layer 3: deterministic review-risk score (0-1) with matched, "
                       "explainable signals for the claim's verbatim.",
        "parameters": {"type": "object", "properties": {
            "claim_id": {"type": "string"}},
            "required": ["claim_id"]}}},
    {"type": "function", "function": {
        "name": "route_to_adjuster",
        "description": "Route a flagged claim to a human adjuster in the case-management "
                       "system. Idempotent. Never approves or denies a claim.",
        "parameters": {"type": "object", "properties": {
            "claim_id": {"type": "string"},
            "reason": {"type": "string", "description": "Human-readable reason incl. "
                                                         "matched risk signals"}},
            "required": ["claim_id", "reason"]}}},
    {"type": "function", "function": {
        "name": "list_claims",
        "description": "List all claims (id, type, region, status, amount).",
        "parameters": {"type": "object", "properties": {}}}},
]

# Names exposed by the MCP server — parity is asserted in tests.
MCP_TOOL_NAMES = [
    "list_claims", "get_claim_status", "summarize_claim",
    "answer_policy_question", "assess_review_risk", "route_to_adjuster",
]


# --------------------------------------------------------------------------
# Shared service functions (used by direct handlers AND MCP tools)
# --------------------------------------------------------------------------
def svc_list(db: ClaimsDB) -> dict:
    return {"claims": [
        {"claim_id": c["claim_id"], "claim_type": c["claim_type"],
         "region": c["region"], "status": c["status"],
         "claimed_amount": c.get("claimed_amount")}
        for c in db.list()]}


def svc_get_status(db: ClaimsDB, claim_id: str) -> dict:
    c = db.get(claim_id)
    return {"claim_id": claim_id, "status": c["status"],
            "claim_type": c["claim_type"], "region": c["region"],
            "reported_date": c.get("reported_date"),
            "claimed_amount": c.get("claimed_amount")}


def svc_summarize(llm, db: ClaimsDB, claim_id: str) -> dict:
    claim = db.get(claim_id)
    if llm is None:
        return {"claim_id": claim_id, "incident": claim["verbatim"][:200],
                "key_facts": [], "evidence_present": [],
                "gaps_or_inconsistencies": [],
                "plain_language": claim["verbatim"],
                "source_claim_id": claim_id}
    return llm.summarize_claim(claim)


def svc_answer(llm, rag: PolicyRAG, db: ClaimsDB, claim_id: str,
               question: str) -> dict:
    c = db.get(claim_id)
    result = rag.answer(question, c["claim_type"], c["region"])
    result.update({"claim_id": claim_id, "claim_type": c["claim_type"],
                   "region": c["region"]})
    return result


def svc_risk(db: ClaimsDB, claim_id: str) -> dict:
    claim = db.get(claim_id)
    scored = risk.score_claim(claim)
    scored["claim_id"] = claim_id
    scored["threshold"] = config.RISK_THRESHOLD
    scored["flagged"] = scored["score"] >= config.RISK_THRESHOLD
    scored["borderline"] = compliance.borderline(scored)
    return scored


def svc_route(llm, db: ClaimsDB, queue: CaseQueue, claim_id: str,
              reason: str) -> dict:
    claim = db.get(claim_id)
    scored = risk.score_claim(claim)
    gate = compliance.check_routing(claim, scored,
                                    already_routed=queue.already_routed(claim_id))
    if not gate["allowed"]:
        existing = next((e for e in queue.list() if e["claim_id"] == claim_id), None)
        return {"routed": False, "blocked": True, "gate": gate, "entry": existing,
                "note": "routing blocked by compliance gate: "
                        + "; ".join(gate["blockers"])}
    summary = svc_summarize(llm, db, claim_id) if llm is not None else None
    model_context = None
    try:
        from .severity import predict_claim
        model_context = predict_claim(claim)
    except Exception:
        model_context = None
    return queue.route(claim_id, reason=reason, risk=scored, summary=summary,
                       model_context=model_context)


def build_direct_handlers(llm, db: ClaimsDB, rag: PolicyRAG,
                          queue: CaseQueue) -> dict[str, callable]:
    """name -> handler for the in-process (direct) transport."""
    return {
        "list_claims": lambda: svc_list(db),
        "get_claim_status": lambda claim_id: svc_get_status(db, claim_id),
        "summarize_claim": lambda claim_id: svc_summarize(llm, db, claim_id),
        "answer_policy_question": lambda claim_id, question:
            svc_answer(llm, rag, db, claim_id, question),
        "assess_review_risk": lambda claim_id: svc_risk(db, claim_id),
        "route_to_adjuster": lambda claim_id, reason:
            svc_route(llm, db, queue, claim_id, reason),
    }
