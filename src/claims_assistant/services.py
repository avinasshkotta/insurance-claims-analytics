"""Backends behind the single interface (Layer 4): claims database,
document store access, and case-management routing."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import config


class ClaimsDB:
    def __init__(self, path: Path | None = None):
        self.path = path or config.CLAIMS_PATH
        self._claims = {c["claim_id"]: c
                        for c in json.loads(self.path.read_text(encoding="utf-8"))}

    def list(self) -> list[dict]:
        return list(self._claims.values())

    def get(self, claim_id: str) -> dict:
        if claim_id not in self._claims:
            raise KeyError(f"unknown claim_id: {claim_id}")
        return self._claims[claim_id]

    def next_claim_id(self) -> str:
        numbers = [int(m.group(1)) for cid in self._claims
                   for m in [re.match(r"CLM-(\d+)$", cid)] if m]
        return f"CLM-{(max(numbers) if numbers else 2000) + 1}"

    def add(self, claim: dict) -> dict:
        if claim["claim_id"] in self._claims:
            raise ValueError(f"duplicate claim_id: {claim['claim_id']}")
        self._claims[claim["claim_id"]] = claim
        self.path.write_text(
            json.dumps(list(self._claims.values()), indent=2, ensure_ascii=False),
            encoding="utf-8")
        return claim


class CaseQueue:
    """Case-management queue. Idempotent: a claim already routed is not duplicated."""

    def __init__(self, path: Path | None = None):
        self.path = path or config.CASE_QUEUE_PATH
        self.entries: list[dict] = []
        if self.path.exists():
            self.entries = json.loads(self.path.read_text(encoding="utf-8"))

    def already_routed(self, claim_id: str) -> bool:
        return any(e["claim_id"] == claim_id for e in self.entries)

    def route(self, claim_id: str, reason: str, risk: dict,
              summary: dict | None = None,
              model_context: dict | None = None) -> dict:
        if self.already_routed(claim_id):
            existing = next(e for e in self.entries if e["claim_id"] == claim_id)
            return {"routed": False, "entry": existing,
                    "note": "already routed — idempotent skip"}
        entry = {
            "claim_id": claim_id,
            "reason": reason,
            "risk_score": risk.get("score"),
            "risk_signals": risk.get("matched", []),
            "summary_headline": (summary or {}).get("incident"),
            "model_context": model_context,
            "assigned_to": "human_adjuster",
            "status": "pending_review",
            "human_decision": None,
            "decision_reason": None,
            "decided_at": None,
            "routed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self.entries.append(entry)
        self.path.write_text(json.dumps(self.entries, indent=2), encoding="utf-8")
        return {"routed": True, "entry": entry}

    def record_decision(self, claim_id: str, decision: str,
                        reason: str | None = None) -> dict:
        """Capture the human outcome: accepted the routing or overrode it."""
        if decision not in {"accepted", "overridden"}:
            raise ValueError("decision must be 'accepted' or 'overridden'")
        entry = next((e for e in self.entries if e["claim_id"] == claim_id), None)
        if entry is None:
            raise KeyError(f"claim not in queue: {claim_id}")
        entry["human_decision"] = decision
        entry["decision_reason"] = reason
        entry["decided_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.path.write_text(json.dumps(self.entries, indent=2), encoding="utf-8")
        return entry

    def stats(self) -> dict:
        total = len(self.entries)
        accepted = sum(1 for e in self.entries if e.get("human_decision") == "accepted")
        overridden = sum(1 for e in self.entries if e.get("human_decision") == "overridden")
        decided = accepted + overridden
        return {
            "total": total,
            "pending": total - decided,
            "accepted": accepted,
            "overridden": overridden,
            "override_rate": round(overridden / decided, 4) if decided else None,
        }

    def list(self) -> list[dict]:
        return list(self.entries)
