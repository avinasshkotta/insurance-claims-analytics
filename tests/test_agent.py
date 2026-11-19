"""Layer 3 agent loop + Layer 4 transport parity."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claims_assistant.agent import run_agent  # noqa: E402
from claims_assistant.rag import PolicyRAG  # noqa: E402
from claims_assistant.services import CaseQueue, ClaimsDB  # noqa: E402
from claims_assistant.toolkit import (MCP_TOOL_NAMES,  # noqa: E402
                                      TOOL_SCHEMAS, build_direct_handlers)

SCHEMA_NAMES = [t["function"]["name"] for t in TOOL_SCHEMAS]


def _handlers(fake_llm, tmp_path):
    return build_direct_handlers(fake_llm, ClaimsDB(), PolicyRAG(llm=None),
                                 CaseQueue(tmp_path / "case_queue.json"))


def test_tool_surface_parity():
    """Layer 4 constraint: the agent's tools and the MCP server's tools match."""
    assert sorted(SCHEMA_NAMES) == sorted(MCP_TOOL_NAMES)


def test_mcp_servers_register_same_tools():
    """The three per-system servers together expose exactly the six tools."""
    import asyncio as aio

    from claims_assistant import (mcp_case_server, mcp_claims_server,
                                  mcp_docs_server)

    names: list[str] = []
    for module in (mcp_claims_server, mcp_docs_server, mcp_case_server):
        names += [t.name for t in aio.run(module.mcp.list_tools())]
    assert sorted(names) == sorted(MCP_TOOL_NAMES)
    assert len(names) == len(set(names)), "tool names must be unique across servers"


def test_agent_routes_flagged_claim(fake_llm, tmp_path):
    """Scripted agent: status -> risk -> route for a flagged claim."""
    fake_llm.script = [
        {"content": None, "tool_calls": [
            {"id": "1", "name": "get_claim_status",
             "arguments": {"claim_id": "CLM-1043"}}]},
        {"content": None, "tool_calls": [
            {"id": "2", "name": "assess_review_risk",
             "arguments": {"claim_id": "CLM-1043"}}]},
        {"content": None, "tool_calls": [
            {"id": "3", "name": "route_to_adjuster",
             "arguments": {"claim_id": "CLM-1043",
                           "reason": "risk >= threshold: late_reporting, "
                                     "informal_payment, pressure_full_amount"}}]},
        {"content": "CLM-1043 | ROUTE | 0.55 | routed to human adjuster",
         "tool_calls": []},
    ]
    result = asyncio.run(run_agent(["CLM-1043"], None, fake_llm,
                                   handlers=_handlers(fake_llm, tmp_path)))
    assert [t["tool"] for t in result["tool_log"]] == [
        "get_claim_status", "assess_review_risk", "route_to_adjuster"]
    assert "ROUTE" in result["final"]
    assert "human" in result["final"].lower()

    # the route actually landed in case management
    entries = (tmp_path / "case_queue.json")
    assert entries.exists(), "route_to_adjuster must persist to case management"


def test_agent_never_asks_for_denial_tools(fake_llm, tmp_path):
    """Constraint: the tool surface offers no approve/deny/settle capability."""
    banned = {"approve_claim", "deny_claim", "settle_claim", "close_claim"}
    assert not (set(SCHEMA_NAMES) & banned)


def test_agent_stops_on_max_turns(fake_llm, tmp_path):
    """Safety: a looping model cannot run forever."""
    looping = {"content": None, "tool_calls": [
        {"id": "x", "name": "list_claims", "arguments": {}}]}
    fake_llm.script = [looping]  # replays forever
    result = asyncio.run(run_agent(["CLM-1041"], None, fake_llm,
                                   handlers=_handlers(fake_llm, tmp_path),
                                   max_turns=3))
    assert result["final"] == "MAX_TURNS exceeded"
    assert len(result["tool_log"]) == 3


def test_direct_transport_reports_tool_errors(fake_llm, tmp_path):
    """A bad tool call doesn't kill the loop — the model sees the error."""
    fake_llm.script = [
        {"content": None, "tool_calls": [
            {"id": "1", "name": "get_claim_status",
             "arguments": {"claim_id": "CLM-9999"}}]},  # unknown claim
        {"content": "CLM-9999 not found; stopping.", "tool_calls": []},
    ]
    result = asyncio.run(run_agent(["CLM-9999"], None, fake_llm,
                                   handlers=_handlers(fake_llm, tmp_path)))
    assert result["tool_log"][0]["ok"] is False
    assert "not found" in result["final"]
