"""Embeddings: vector cache, cosine, and hybrid retrieval with BM25 fallback."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claims_assistant.embeddings import (EmbeddingIndex, chunk_key,  # noqa: E402
                                         cosine, reciprocal_rank_fusion)
from claims_assistant.rag import PolicyRAG  # noqa: E402


def test_cosine():
    assert cosine([1, 0], [1, 0]) == 1.0
    assert cosine([1, 0], [0, 1]) == 0.0
    assert cosine([], [1]) == 0.0


def test_chunk_key_changes_with_content():
    a = {"doc_id": "D", "section": "S", "text": "hello"}
    b = {"doc_id": "D", "section": "S", "text": "hello world"}
    assert chunk_key(a) != chunk_key(b)


def test_build_caches_and_is_idempotent(tmp_path, fake_llm):
    idx = EmbeddingIndex(path=tmp_path / "emb.json", model="test-model")
    chunks = [
        {"doc_id": "D1", "section": "Rental", "text": "rental rental limit"},
        {"doc_id": "D1", "section": "Deadline", "text": "filing deadline"},
    ]
    assert idx.build(chunks, fake_llm.embed) == 2
    assert len(idx) == 2
    assert idx.build(chunks, fake_llm.embed) == 0, "second build must hit the cache"
    assert idx.covers(chunks)

    reloaded = EmbeddingIndex(path=tmp_path / "emb.json", model="test-model")
    assert len(reloaded) == 2


def test_model_change_invalidates_cache(tmp_path, fake_llm):
    path = tmp_path / "emb.json"
    idx = EmbeddingIndex(path=path, model="model-a")
    idx.build([{"doc_id": "D", "section": "S", "text": "rental"}], fake_llm.embed)
    other = EmbeddingIndex(path=path, model="model-b")
    assert len(other) == 0, "different embedding model must not reuse vectors"


def test_rrf_prefers_agreement():
    a, b, c = ({"doc_id": "D", "section": s, "text": s} for s in ("a", "b", "c"))
    fused = reciprocal_rank_fusion([[a, b, c], [c, a, b]])
    assert fused[0] is a, "chunk ranked highly by both lists should win"


def test_hybrid_retrieval_mode_and_result(tmp_path, fake_llm):
    rag = PolicyRAG(llm=fake_llm)
    idx = EmbeddingIndex(path=tmp_path / "emb.json", model="test-model")
    idx.build(rag.all_chunks(), fake_llm.embed)
    rag.embeddings = idx

    hits = rag.retrieve("rental reimbursement limit", "auto", "CA")
    assert rag._last_mode == "hybrid"
    assert hits and hits[0]["doc_id"] == "POLICY-AUTO-CA"


def test_falls_back_to_bm25_without_index(tmp_path, fake_llm):
    rag = PolicyRAG(llm=fake_llm)  # no embeddings attached
    hits = rag.retrieve("rental reimbursement", "auto", "CA")
    assert rag._last_mode == "bm25"
    assert hits and hits[0]["doc_id"] == "POLICY-AUTO-CA"
