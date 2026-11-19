"""MCP server #1 — the claims database.

Owns read access to claims: listing, status, and deterministic risk scoring.
"""
from __future__ import annotations

from .mcp_common import make_server, require_token
from .services import ClaimsDB
from .toolkit import svc_get_status, svc_list, svc_risk

mcp = make_server("claims-db")
_db = ClaimsDB()


@mcp.tool()
def list_claims() -> dict:
    """List all claims in the claims database (id, type, region, status, amount)."""
    return svc_list(_db)


@mcp.tool()
def get_claim_status(claim_id: str) -> dict:
    """Check the current status of a claim in the claims database."""
    return svc_get_status(_db, claim_id)


@mcp.tool()
def assess_review_risk(claim_id: str) -> dict:
    """Deterministic review-risk score (0-1) with matched, explainable signals."""
    return svc_risk(_db, claim_id)


if __name__ == "__main__":
    require_token("claims-db")
    mcp.run()
