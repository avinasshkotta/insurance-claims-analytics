"""Layer 2 — RAG: retrieve the actual policy doc for claim type + region,
answer strictly from retrieved passages, or refuse.

Constraint enforced in code: no retrieved passage → no answer. Never from memory.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from . import config, guardrails
from .embeddings import EmbeddingIndex, reciprocal_rank_fusion
from .prompts import prompt_text

# The grounding prompt lives in prompts/rag_answer.md (versioned).


# --------------------------------------------------------------------------
# Document loading & chunking
# --------------------------------------------------------------------------
def load_policy_docs(policy_dir: Path | None = None) -> list[dict]:
    """Load markdown policy docs with YAML-ish frontmatter into section chunks."""
    policy_dir = policy_dir or config.POLICY_DIR
    docs: list[dict] = []
    for path in sorted(policy_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        meta: dict[str, str] = {}
        body = text
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                for line in parts[1].strip().splitlines():
                    if ":" in line:
                        key, _, value = line.partition(":")
                        meta[key.strip()] = value.strip()
                body = parts[2]
        doc_id = meta.get("doc_id", path.stem)
        # Chunk by markdown sections (## headings)
        sections = re.split(r"^(## .+)$", body, flags=re.M)
        chunks: list[dict] = []
        current = "Preamble"
        for piece in sections:
            if piece.startswith("## "):
                current = piece[3:].strip()
            elif piece.strip():
                chunks.append({"doc_id": doc_id, "section": current,
                               "text": " ".join(piece.split()),
                               "claim_type": meta.get("claim_type", ""),
                               "region": meta.get("region", ""),
                               "title": meta.get("title", doc_id)})
        docs.append({"doc_id": doc_id, "meta": meta, "chunks": chunks})
    return docs


# --------------------------------------------------------------------------
# BM25 over chunks (pure python, no embeddings needed for the demo)
# --------------------------------------------------------------------------
_TOKEN = re.compile(r"[a-z0-9]+")

# Small stopword list — without it, "what is the ..." style questions let
# frequent function words (and near-unique words like "limit") drown out
# the actual section match.
_STOP = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "of", "for",
    "to", "in", "on", "at", "and", "or", "what", "which", "who", "whom",
    "how", "when", "why", "does", "did", "do", "my", "your", "our", "i",
    "we", "you", "it", "this", "that", "with", "as", "by", "from", "not",
    "no", "can", "could", "would", "should", "may", "might", "will",
    "about", "into", "per", "up",
}


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


class BM25Index:
    def __init__(self, chunks: list[dict], k1: float = 1.5, b: float = 0.75,
                 title_boost: float = 2.0):
        self.chunks = chunks
        self.k1, self.b, self.title_boost = k1, b, title_boost
        self.doc_tokens = [_tokenize(c["text"]) for c in chunks]
        # Section headings are strong relevance signals: a query matching the
        # section title should outrank a stray keyword elsewhere in the doc.
        self.title_sets = [set(_tokenize(c.get("section", ""))) for c in chunks]
        self.doc_len = [len(t) for t in self.doc_tokens]
        self.avgdl = (sum(self.doc_len) / len(self.doc_len)) if self.doc_len else 0.0
        self.tfs: list[dict[str, int]] = []
        df: dict[str, int] = {}
        for tokens in self.doc_tokens:
            tf: dict[str, int] = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1
            self.tfs.append(tf)
            for t in tf:
                df[t] = df.get(t, 0) + 1
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def search(self, query: str, k: int = 4) -> list[tuple[float, dict]]:
        q_tokens = _tokenize(query)
        scored: list[tuple[float, dict]] = []
        for i, chunk in enumerate(self.chunks):
            score = 0.0
            for t in q_tokens:
                tf = self.tfs[i].get(t, 0)
                if not tf:
                    continue
                idf = self.idf.get(t, 0.0)
                denom = tf + self.k1 * (1 - self.b + self.b * self.doc_len[i] / self.avgdl)
                score += idf * tf * (self.k1 + 1) / denom
            # title boost for distinct matching query terms
            score += self.title_boost * sum(
                self.idf.get(t, 0.0) for t in set(q_tokens)
                if t in self.title_sets[i])
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda x: -x[0])
        return scored[:k]


class PolicyRAG:
    """Retrieve-then-answer. Refuses when retrieval is empty.

    Retrieval is hybrid when a semantic index exists and a model is available:
    BM25 + embeddings fused with reciprocal rank fusion. Otherwise BM25 only.
    """

    def __init__(self, llm=None, policy_dir: Path | None = None,
                 embeddings: EmbeddingIndex | None = None):
        self.llm = llm
        self.docs = load_policy_docs(policy_dir)
        self.embeddings = embeddings
        self._last_mode = "bm25"

    def all_chunks(self) -> list[dict]:
        return [c for doc in self.docs for c in doc["chunks"]]

    def _eligible(self, claim_type: str, region: str) -> list[dict]:
        return [c for doc in self.docs for c in doc["chunks"]
                if c["claim_type"] == claim_type and c["region"] == region]

    def retrieve(self, question: str, claim_type: str, region: str,
                 k: int = 4) -> list[dict]:
        """Filter by claim type + region FIRST, then rank within that set."""
        eligible = self._eligible(claim_type, region)
        if not eligible:
            return []

        bm25_ranked = [chunk for _, chunk in
                       BM25Index(eligible).search(question, len(eligible))]

        if (self.embeddings is not None and self.llm is not None
                and self.embeddings.covers(eligible)):
            query_vec = self.llm.embed([question])[0]
            semantic_ranked = [chunk for _, chunk in
                               self.embeddings.similarities(query_vec, eligible)]
            self._last_mode = "hybrid"
            return reciprocal_rank_fusion([bm25_ranked, semantic_ranked])[:k]

        self._last_mode = "bm25"
        return bm25_ranked[:k]

    def answer(self, question: str, claim_type: str, region: str) -> dict:
        scan = guardrails.scan_input(question)
        if scan["blocked"]:
            return {"answer": "BLOCKED: input rejected by guardrails ("
                              + ", ".join(scan["matches"])
                              + "). Escalated to a human — not answered.",
                    "citations": [], "retrieved": 0, "retrieval": "blocked",
                    "guardrails": scan}

        safe_question, pii = guardrails.redact_pii(question)
        hits = self.retrieve(safe_question, claim_type, region)
        hits, dropped = guardrails.sanitize_retrieved(hits)
        if not hits:
            return {"answer": "REFUSED: no policy document retrieved for "
                              f"claim_type={claim_type}, region={region}. "
                              "Escalating to a human — not answering from memory.",
                    "citations": [], "retrieved": 0,
                    "retrieval": getattr(self, "_last_mode", "bm25"),
                    "guardrails": {**scan, "pii_redacted": pii,
                                   "dropped_chunks": dropped}}
        excerpts = "\n\n".join(
            f"[{h['doc_id']} · {h['section']}]\n{h['text']}" for h in hits)
        if self.llm is None:
            # Deterministic fallback used in tests: return top excerpt verbatim.
            top = hits[0]
            return {"answer": top["text"],
                    "citations": [f"{top['doc_id']} · {top['section']}"],
                    "retrieved": len(hits), "retrieval": self._last_mode,
                    "guardrails": {**scan, "pii_redacted": pii,
                                   "dropped_chunks": dropped}}
        answer = self.llm.chat(
            prompt_text("rag_answer"),
            f"Policy excerpts:\n{excerpts}\n\nQuestion: {safe_question}",
            kind="answer")
        citations = [f"{h['doc_id']} · {h['section']}" for h in hits]
        check = guardrails.check_output(
            answer, {(h["doc_id"], h["section"]) for h in hits})
        return {"answer": answer, "citations": citations,
                "retrieved": len(hits), "retrieval": self._last_mode,
                "guardrails": {**scan, "pii_redacted": pii,
                               "dropped_chunks": dropped,
                               "invalid_citations": check["invalid"]}}
