"""Human decision capture + override-rate metrics."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claims_assistant.services import CaseQueue  # noqa: E402


def make_queue(tmp_path) -> CaseQueue:
    return CaseQueue(tmp_path / "queue.json")


def test_record_decision_and_stats(tmp_path):
    q = make_queue(tmp_path)
    q.route("CLM-1", "reason", {"score": 0.5, "matched": []})
    q.route("CLM-2", "reason", {"score": 0.75, "matched": []})

    q.record_decision("CLM-1", "accepted")
    q.record_decision("CLM-2", "overridden", "storm damage was pre-existing")

    stats = q.stats()
    assert stats == {"total": 2, "pending": 0, "accepted": 1,
                     "overridden": 1, "override_rate": 0.5}
    entry = next(e for e in q.list() if e["claim_id"] == "CLM-2")
    assert entry["decision_reason"] == "storm damage was pre-existing"
    assert entry["decided_at"]


def test_decision_persists(tmp_path):
    q = make_queue(tmp_path)
    q.route("CLM-1", "reason", {"score": 0.5, "matched": []})
    q.record_decision("CLM-1", "accepted")
    reloaded = CaseQueue(tmp_path / "queue.json")
    assert reloaded.list()[0]["human_decision"] == "accepted"


def test_pending_before_decision(tmp_path):
    q = make_queue(tmp_path)
    q.route("CLM-1", "reason", {"score": 0.5, "matched": []})
    stats = q.stats()
    assert stats["pending"] == 1
    assert stats["override_rate"] is None


def test_invalid_decision_rejected(tmp_path):
    q = make_queue(tmp_path)
    q.route("CLM-1", "reason", {"score": 0.5, "matched": []})
    with pytest.raises(ValueError):
        q.record_decision("CLM-1", "maybe")


def test_unknown_claim_rejected(tmp_path):
    q = make_queue(tmp_path)
    with pytest.raises(KeyError):
        q.record_decision("CLM-999", "accepted")
