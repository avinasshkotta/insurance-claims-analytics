"""MCP server #2 — the document store.

Owns verbatim summarization (Layer 1) and grounded policy Q&A (Layer 2).
"""
from __future__ import annotations

from .llm import LLMClient
from .mcp_common import make_server, require_token
from .rag import PolicyRAG
from .services import ClaimsDB
from .toolkit import svc_answer, svc_summarize

mcp = make_server("document-store")
_db = ClaimsDB()
_llm: LLMClient | None = None


def _model() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = LLMClient()
    return _llm


@mcp.tool()
def summarize_claim(claim_id: str) -> dict:
    """Layer 1: plain-language structured summary of the claim verbatim."""
    return svc_summarize(_model(), _db, claim_id)


@mcp.tool()
def answer_policy_question(claim_id: str, question: str) -> dict:
    """Layer 2: answer only from retrieved policy documents for this claim's
    type and region; refuses if nothing is retrieved."""
    return svc_answer(_model(), PolicyRAG(llm=_model()), _db, claim_id, question)


if __name__ == "__main__":
    require_token("document-store")
    mcp.run()
