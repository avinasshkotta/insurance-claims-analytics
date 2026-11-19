"""MCP server #3 — case management.

Owns the only write action in the system: routing a flagged claim to a human
adjuster. Least-privilege by construction — the claims and docs servers cannot
write to the queue.
"""
from __future__ import annotations

from .llm import LLMClient
from .mcp_common import make_server, require_token
from .services import CaseQueue, ClaimsDB
from .toolkit import svc_route

mcp = make_server("case-management")
_db = ClaimsDB()
_queue = CaseQueue()
_llm: LLMClient | None = None


def _model() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = LLMClient()
    return _llm


@mcp.tool()
def route_to_adjuster(claim_id: str, reason: str) -> dict:
    """Route a flagged claim to a human adjuster. Idempotent. Never approves,
    denies, or settles a claim."""
    return svc_route(_model(), _db, _queue, claim_id, reason)


if __name__ == "__main__":
    require_token("case-management")
    mcp.run()
