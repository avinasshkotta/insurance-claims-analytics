"""Layer 3 risk rules: deterministic, explainable, correctly triages samples."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claims_assistant import config, risk  # noqa: E402

CLAIMS = {c["claim_id"]: c for c in
          __import__("json").loads(
              (config.CLAIMS_PATH).read_text(encoding="utf-8"))}


def test_risky_claims_flagged_at_threshold():
    for cid in ("CLM-1042", "CLM-1043", "CLM-1046"):
        result = risk.score_claim(CLAIMS[cid])
        assert result["score"] >= config.RISK_THRESHOLD, \
            f"{cid} should be flagged, got {result['score']}"


def test_routine_claims_pass_through():
    for cid in ("CLM-1041", "CLM-1044", "CLM-1045"):
        result = risk.score_claim(CLAIMS[cid])
        assert result["score"] < config.RISK_THRESHOLD, \
            f"{cid} should pass, got {result['score']}"


def test_every_match_is_explainable():
    """Constraint: every flag carries a human-readable reason + evidence."""
    result = risk.score_claim(CLAIMS["CLM-1043"])
    assert result["matched"], "expected matched signals"
    for m in result["matched"]:
        assert m["description"], "each match needs a description"
        assert m["evidence"], "each match needs evidence"
        assert 0 < m["weight"] <= 1


def test_deterministic():
    a = risk.score_claim(CLAIMS["CLM-1042"])
    b = risk.score_claim(CLAIMS["CLM-1042"])
    assert a == b, "identical inputs must produce identical triage"
