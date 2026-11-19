"""Layer 3 core — deterministic, explainable review-risk rules.

The Claim Verbatims review-risk pattern is encoded here as weighted signals.
Scores are rule-driven (identical inputs → identical triage); every matched
signal carries human-readable evidence for the routing record.
"""
from __future__ import annotations

import re
from datetime import date

LATE_REPORTING_DAYS = 14

# (signal_id, weight, description, evidence regex or special)
TEXT_SIGNALS: list[tuple[str, float, str, re.Pattern]] = [
    ("urgency_pressure", 0.25, "Urgency/pressure language around payment",
     re.compile(r"need (?:the )?payout|this week|asap|immediately|holding it for me",
                re.I)),
    ("no_police_report", 0.15, "Incident reported but no official report filed",
     re.compile(r"(?:have )?not filed (?:a )?police report|no police report", re.I)),
    ("informal_third_party", 0.10, "Reliance on informal third parties",
     re.compile(r"cousin|friend of a friend|buddy|my guy", re.I)),
    ("unofficial_opinion", 0.10, "Non-official assessment presented as authority",
     re.compile(r"not an official person|his opinion|says it (?:looked|was)", re.I)),
    ("informal_payment", 0.15, "Cash/informal payment without documentation",
     re.compile(r"paid (?:the )?(?:in cash|cash)|cash, no receipts|no receipts", re.I)),
    ("missing_documentation", 0.10, "Key evidence acknowledged as missing",
     re.compile(r"no (?:photos|receipts|invoice)|without (?:a )?receipt", re.I)),
    ("address_change", 0.15, "Payment address differs from file address",
     re.compile(r"PO box different|different from the address on file", re.I)),
    ("provider_mismatch", 0.20, "Rendering provider absent from network records",
     re.compile(r"does not appear in our provider network|not in (?:the )?network records",
                re.I)),
    ("no_prior_auth", 0.15, "Service documented without prior authorization/treatment",
     re.compile(r"No prior (?:conservative treatment|authorization) documented", re.I)),
    ("pressure_full_amount", 0.15, "Demand for full amount without itemization",
     re.compile(r"want the full amount|pay (?:me|the) full", re.I)),
]

HIGH_AMOUNT = 25_000.0
HIGH_AMOUNT_SIGNAL = ("high_amount_undocumented", 0.15,
                      "High claimed amount with no itemized documentation mentioned")


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def score_claim(claim: dict) -> dict:
    """Score review risk for a claim. Returns score in [0,1] + matched signals."""
    verbatim = claim.get("verbatim", "")
    matched: list[dict] = []

    incident = _parse_date(claim.get("incident_date"))
    reported = _parse_date(claim.get("reported_date"))
    if incident and reported and (reported - incident).days > LATE_REPORTING_DAYS:
        matched.append({
            "signal": "late_reporting",
            "weight": 0.15,
            "description": f"Reported {(reported - incident).days} days after incident "
                           f"(> {LATE_REPORTING_DAYS} days)",
            "evidence": f"{claim.get('incident_date')} → {claim.get('reported_date')}",
        })

    for signal_id, weight, description, pattern in TEXT_SIGNALS:
        m = pattern.search(verbatim)
        if m:
            matched.append({"signal": signal_id, "weight": weight,
                            "description": description, "evidence": m.group(0)})

    amount = float(claim.get("claimed_amount") or 0)
    doc_words = re.search(r"photo|invoice|receipt|police report|itemized|estimate",
                          verbatim, re.I)
    if amount >= HIGH_AMOUNT and not doc_words:
        signal_id, weight, description = HIGH_AMOUNT_SIGNAL
        matched.append({"signal": signal_id, "weight": weight,
                        "description": description,
                        "evidence": f"claimed_amount={amount:,.0f}, no documentation terms"})

    score = min(1.0, round(sum(m["weight"] for m in matched), 2))
    return {"score": score, "matched": matched}
