"""Shared toolkit: services behave identically for any transport (Layer 4)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from claims_assistant.services import CaseQueue, ClaimsDB  # noqa: E402
from claims_assistant.toolkit import (build_direct_handlers,  # noqa: E402
                                      svc_get_status, svc_risk)


@pytest.fixture
def queue(tmp_path):
    return CaseQueue(tmp_path / "case_queue.json")


@pytest.fixture
def handlers(fake_llm, queue):
    return build_direct_handlers(fake_llm, ClaimsDB(),
                                 __import__("claims_assistant.rag",
                                            fromlist=["PolicyRAG"]).PolicyRAG(llm=None),
                                 queue)


def test_get_status(handlers):
    status = handlers["get_claim_status"](claim_id="CLM-1041")
    assert status["status"] == "open"
    assert status["claim_type"] == "auto"
    assert status["region"] == "CA"


def test_unknown_claim_raises():
    with pytest.raises(KeyError):
        svc_get_status(ClaimsDB(), "CLM-9999")


def test_risk_tool_flags(handlers):
    result = handlers["assess_review_risk"](claim_id="CLM-1046")
    assert result["flagged"] is True
    assert result["score"] >= result["threshold"]
    assert result["matched"], "flag must carry explainable signals"


def test_route_is_idempotent(handlers, queue):
    reason = "risk 0.50: urgency_pressure, no_police_report, informal_third_party"
    first = handlers["route_to_adjuster"](claim_id="CLM-1042", reason=reason)
    second = handlers["route_to_adjuster"](claim_id="CLM-1042", reason=reason)
    assert first["routed"] is True
    assert second["routed"] is False, "re-routing must not duplicate work"
    assert len(queue.list()) == 1


def test_route_record_carries_audit_context(handlers):
    """Constraint: routed records include score, reasons, and Layer-1 summary."""
    entry = handlers["route_to_adjuster"](
        claim_id="CLM-1042", reason="test reason")["entry"]
    assert entry["risk_score"] is not None
    assert entry["risk_signals"], "risk signals must travel with the record"
    assert entry["summary_headline"], "Layer-1 summary headline must travel"
    assert entry["assigned_to"] == "human_adjuster"
    assert entry["status"] == "pending_review"
    assert entry["reason"] == "test reason"


def test_summarize_is_structured(handlers):
    summary = handlers["summarize_claim"](claim_id="CLM-1041")
    for key in ("incident", "key_facts", "evidence_present",
                "gaps_or_inconsistencies", "plain_language"):
        assert key in summary, f"Layer-1 summary missing {key}"
    assert summary["source_claim_id"] == "CLM-1041"
