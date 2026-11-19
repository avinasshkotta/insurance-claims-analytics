"""Layer 1: prompt building must survive the literal JSON braces (regression —
a str.format() on the prompt raised KeyError on live runs but not under FakeLLM)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claims_assistant.llm import _redact_pii, _summary_prompt  # noqa: E402

CLAIM = {
    "claim_id": "CLM-1043", "claim_type": "home", "region": "TX",
    "incident_date": "2026-08-30", "reported_date": "2026-09-18",
    "verbatim": "Water damage from the storm. SSN 123-45-6789 on file. "
                "My phone is 555-123-4567.",
}


def test_prompt_builds_without_keyerror():
    """Regression: JSON braces in the prompt must not be parsed as format fields."""
    prompt = _summary_prompt(CLAIM)
    assert "CLM-1043" in prompt          # meta substituted
    assert "Water damage" in prompt      # verbatim substituted
    assert "{meta}" not in prompt
    assert "{verbatim}" not in prompt
    assert '"incident"' in prompt        # JSON example preserved intact


def test_pii_redacted_before_prompt():
    prompt = _summary_prompt(CLAIM)
    assert "123-45-6789" not in prompt, "SSN must be redacted"
    assert "***-**-****" in prompt
    assert "555-123-4567" not in prompt, "phone must be redacted"


def test_redact_keeps_normal_text():
    assert "rear-ended at the stoplight" in _redact_pii("rear-ended at the stoplight")
