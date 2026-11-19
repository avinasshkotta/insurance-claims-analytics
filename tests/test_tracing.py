"""Persisted run traces: audit entries and listing."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claims_assistant.tracing import list_runs, record_run  # noqa: E402


def test_record_and_list(tmp_path):
    path = tmp_path / "runs.jsonl"
    r1 = record_run("triage", ["CLM-1041"],
                    [{"tool": "get_claim_status", "ok": True}],
                    "CLM-1041 | PASS | 0.00", model="gpt-4o-mini", path=path)
    r2 = record_run("api_triage", ["CLM-1042"],
                    [{"tool": "route_to_adjuster", "ok": True},
                     {"tool": "summarize_claim", "ok": False}],
                    "CLM-1042 | ROUTE | 0.50", model="gpt-4o-mini", path=path)

    assert r1["run_id"].startswith("run_")
    assert r1["tool_calls"] == 1
    assert r2["tool_errors"] == 1
    runs = list_runs(path=path)
    assert [r["run_id"] for r in runs] == [r2["run_id"], r1["run_id"]]
    assert "triage_system" in runs[0]["prompts"]


def test_list_empty_when_missing(tmp_path):
    assert list_runs(path=tmp_path / "nope.jsonl") == []


def test_limit(tmp_path):
    path = tmp_path / "runs.jsonl"
    for i in range(5):
        record_run("triage", [f"CLM-{i}"], [], "final", path=path)
    assert len(list_runs(limit=2, path=path)) == 2
