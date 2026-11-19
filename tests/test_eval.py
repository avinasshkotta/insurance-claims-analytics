"""Evaluation harness as a regression gate — the golden set must pass offline."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claims_assistant.evaluate import load_golden, run_eval  # noqa: E402


def test_golden_set_passes_offline():
    report = run_eval(write_report=False)
    assert report["mode"] == "offline"
    assert report["passed"], f"metrics below threshold: {report['metrics']}"


def test_all_cases_reported():
    golden = load_golden()
    report = run_eval(write_report=False)
    assert len(report["results"]) == len(golden["cases"])
    assert len(report["results"]) >= 8


def test_refusal_cases_exist_and_refuse():
    report = run_eval(write_report=False)
    refusals = [r for r in report["results"] if r["expected_doc"] is None]
    assert len(refusals) >= 2
    for r in refusals:
        assert r["refused"] and r["checks"]["refusal_ok"], r["id"]


def test_prompts_versioned_in_report():
    report = run_eval(write_report=False)
    assert {"summary", "rag_answer", "triage_system"} <= set(report["prompts"])
    for version in report["prompts"].values():
        assert "+" in version, "version must include the content digest"
