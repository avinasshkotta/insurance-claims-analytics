"""MCP transport trust boundary: servers refuse to start without a token."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claims_assistant.mcp_common import MIN_TOKEN_LEN, require_token  # noqa: E402


def test_refuses_without_token(monkeypatch):
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    with pytest.raises(SystemExit) as exc:
        require_token("claims-db")
    assert exc.value.code == 2


def test_refuses_short_token(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "short")
    with pytest.raises(SystemExit):
        require_token("claims-db")


def test_accepts_valid_token(monkeypatch):
    token = "local-demo-token"
    assert len(token) >= MIN_TOKEN_LEN
    monkeypatch.setenv("MCP_AUTH_TOKEN", token)
    assert require_token("claims-db") == token


def test_each_server_has_isolated_tools():
    """Least privilege: each server owns only its own tool names."""
    import asyncio as aio

    from claims_assistant import (mcp_case_server, mcp_claims_server,
                                  mcp_docs_server)

    claims = {t.name for t in aio.run(mcp_claims_server.mcp.list_tools())}
    docs = {t.name for t in aio.run(mcp_docs_server.mcp.list_tools())}
    case = {t.name for t in aio.run(mcp_case_server.mcp.list_tools())}

    assert claims == {"list_claims", "get_claim_status", "assess_review_risk"}
    assert docs == {"summarize_claim", "answer_policy_question"}
    assert case == {"route_to_adjuster"}, "only case management may write"
    assert not (claims & case) and not (docs & case)
