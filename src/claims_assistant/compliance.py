"""Deterministic governance gate — the "supervisor" without an LLM.

Two jobs:
1. Block routing that should not happen (closed/denied claims, below-threshold
   scores, already-routed claims, signals without evidence).
2. Flag borderline scores (±0.10 of the threshold) with a structured note built
   from the existing rule signals — no extra model calls, fully reproducible.
"""
from __future__ import annotations

from . import config

BAND = 0.10
BLOCKED_STATUSES = {"closed", "denied"}


def borderline(scored: dict, threshold: float | None = None) -> dict | None:
    """Return a structured note when the score sits within ±BAND of the threshold."""
    threshold = config.RISK_THRESHOLD if threshold is None else threshold
    low, high = round(threshold - BAND, 2), round(threshold + BAND, 2)
    score = float(scored.get("score", 0.0))
    if not (low <= score <= high):
        return None
    top = sorted(scored.get("matched", []), key=lambda m: -m["weight"])[:3]
    drivers = "; ".join(
        f'{m["signal"]} ({m["weight"]:.2f}, "{m["evidence"]}")' for m in top
    ) or "no signals matched"
    decision = "route" if score >= threshold else "pass-through"
    return {
        "borderline": True,
        "band": [low, high],
        "decision": decision,
        "note": (f"Score {score:.2f} is within ±{BAND:.2f} of the {threshold:.2f} "
                 f"threshold → {decision}. Drivers: {drivers}."),
    }


def check_routing(claim: dict, scored: dict, already_routed: bool = False,
                  threshold: float | None = None) -> dict:
    """Gate a routing request. Returns allowed + human-readable blockers."""
    threshold = config.RISK_THRESHOLD if threshold is None else threshold
    score = float(scored.get("score", 0.0))
    blockers: list[str] = []
    warnings: list[str] = []

    if claim.get("status") in BLOCKED_STATUSES:
        blockers.append(f"claim status is {claim['status']}")
    if already_routed:
        blockers.append("claim already routed (idempotent skip)")
    if score < threshold:
        blockers.append(f"risk {score:.2f} is below the {threshold:.2f} threshold")

    incomplete = [m for m in scored.get("matched", [])
                  if not m.get("evidence") or not m.get("description")]
    if incomplete:
        blockers.append(f"{len(incomplete)} matched signal(s) lack evidence")

    if not claim.get("claimed_amount"):
        warnings.append("claimed amount missing")

    return {
        "allowed": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "evidence_complete": not incomplete,
        "borderline": borderline(scored, threshold),
    }
