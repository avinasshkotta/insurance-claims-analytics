"""Deterministic compliance gate + borderline notes."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claims_assistant.compliance import borderline, check_routing  # noqa: E402

SIGNAL = {"signal": "late_reporting", "weight": 0.15,
          "description": "Reported late", "evidence": "2026-08-30 → 2026-09-18"}


def scored(score: float, matched=None) -> dict:
    return {"score": score, "matched": matched if matched is not None else [SIGNAL]}


def test_allows_flagged_open_claim_with_evidence():
    gate = check_routing({"status": "open", "claimed_amount": 1000},
                         scored(0.75))
    assert gate["allowed"]
    assert gate["evidence_complete"]


def test_blocks_below_threshold():
    gate = check_routing({"status": "open", "claimed_amount": 1000}, scored(0.30))
    assert not gate["allowed"]
    assert any("below the" in b for b in gate["blockers"])


def test_blocks_closed_and_denied():
    for status in ("closed", "denied"):
        gate = check_routing({"status": status, "claimed_amount": 1000},
                             scored(0.75))
        assert not gate["allowed"]
        assert any(status in b for b in gate["blockers"])


def test_blocks_already_routed():
    gate = check_routing({"status": "open", "claimed_amount": 1000},
                         scored(0.75), already_routed=True)
    assert not gate["allowed"]
    assert any("already routed" in b for b in gate["blockers"])


def test_blocks_signal_without_evidence():
    bad = [{"signal": "x", "weight": 0.2, "description": "", "evidence": ""}]
    gate = check_routing({"status": "open", "claimed_amount": 1000},
                         scored(0.75, bad))
    assert not gate["allowed"]
    assert not gate["evidence_complete"]


def test_borderline_note_inside_band():
    note = borderline(scored(0.50), threshold=0.5)
    assert note and note["borderline"]
    assert note["band"] == [0.4, 0.6]
    assert "late_reporting" in note["note"]


def test_borderline_note_absent_outside_band():
    assert borderline(scored(0.10), threshold=0.5) is None
    assert borderline(scored(0.90), threshold=0.5) is None


def test_borderline_direction_reflects_decision():
    below = borderline(scored(0.45), threshold=0.5)
    above = borderline(scored(0.55), threshold=0.5)
    assert below["decision"] == "pass-through"
    assert above["decision"] == "route"
