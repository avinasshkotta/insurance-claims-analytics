"""Token/cost accounting: records JSONL entries and aggregates them."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claims_assistant import usage  # noqa: E402


def test_estimate_cost_gpt4o_mini():
    # (1000 * 0.15 + 500 * 0.60) / 1e6 = 0.00045
    assert usage.estimate_cost("gpt-4o-mini", 1000, 500) == 0.00045


def test_record_and_summary(tmp_path):
    path = tmp_path / "usage.jsonl"
    usage.record("summarize", "gpt-4o-mini", 1200, 300, path=path)
    usage.record("tools", "gpt-4o-mini", 800, 100, path=path)
    s = usage.summary(path)
    assert s["calls"] == 2
    assert s["input_tokens"] == 2000
    assert s["output_tokens"] == 400
    assert s["cost_usd"] > 0
    assert s["by_kind"]["summarize"]["calls"] == 1
    assert s["by_kind"]["tools"]["input_tokens"] == 800


def test_summary_handles_corrupt_lines(tmp_path):
    path = tmp_path / "usage.jsonl"
    path.write_text('{"kind":"x","input_tokens":1,"output_tokens":1,"cost_usd":0.1}\nnot-json\n',
                    encoding="utf-8")
    s = usage.summary(path)
    assert s["calls"] == 1


def test_unknown_model_uses_fallback_price():
    cost = usage.estimate_cost("mystery-model", 1_000_000, 0)
    assert cost == usage._FALLBACK[0]
