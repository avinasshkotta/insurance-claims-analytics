"""Layer 2 RAG: region+type filter first, then BM25; refuse when nothing retrieved."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claims_assistant.rag import PolicyRAG  # noqa: E402


def test_region_and_type_filter():
    """A CA auto question must only ever retrieve CA auto policy chunks."""
    rag = PolicyRAG(llm=None)
    hits = rag.retrieve("rental reimbursement daily limit", "auto", "CA")
    assert hits, "expected retrieval hits"
    assert all(h["doc_id"] == "POLICY-AUTO-CA" for h in hits), \
        f"cross-region leak: {[h['doc_id'] for h in hits]}"


def test_region_specific_numbers():
    """TX retrieval must surface the TX rental number, not CA's."""
    rag = PolicyRAG(llm=None)
    result = rag.answer("What is the daily rental reimbursement limit?", "auto", "TX")
    assert "$35" in result["answer"], result["answer"]
    assert "$50" not in result["answer"], "CA number leaked into TX answer"


def test_refuses_when_nothing_retrieved():
    """Constraint: no retrieved passage -> refusal, never memory."""
    rag = PolicyRAG(llm=None)
    result = rag.answer("What is my deductible?", "health", "TX")  # no such policy
    assert result["retrieved"] == 0
    assert result["citations"] == []
    assert "REFUSED" in result["answer"]
    assert "memory" in result["answer"]


def test_answer_carries_citations():
    rag = PolicyRAG(llm=None)
    result = rag.answer("filing deadline for claims", "home", "TX")
    assert result["retrieved"] > 0
    assert result["citations"], "answers must cite sources"
    assert all("POLICY-HOME-TX" in c for c in result["citations"])
