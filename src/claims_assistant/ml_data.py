"""Synthetic claims history for the severity model.

Real claims history is not available in this environment, so this generator
produces a plausible, seeded dataset with known structure (type/region effects,
late-reporting and attorney uplift, evidence discount) plus noise. Labels are
explicitly synthetic — the pipeline is the deliverable, not the numbers.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TYPE_FACTOR = {"auto": 0.85, "home": 0.90, "health": 0.75}
REGION_FACTOR = {"CA": 1.05, "TX": 0.95, "FL": 1.10, "NY": 1.00}


def generate_claims(n: int = 2500, seed: int = 42,
                    drift_shift: bool = False) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    claim_type = rng.choice(["auto", "home", "health"], n, p=[0.45, 0.35, 0.20])
    region = rng.choice(["CA", "TX", "FL", "NY"], n, p=[0.30, 0.30, 0.20, 0.20])
    channel = rng.choice(["phone", "web", "provider", "agent"], n,
                         p=[0.35, 0.30, 0.20, 0.15])

    base = np.where(claim_type == "auto", 12_000,
                    np.where(claim_type == "home", 26_000, 9_000))
    claimed = np.clip(rng.lognormal(np.log(base), 0.7), 300, 500_000)

    days = rng.gamma(2.0, 4.0, n).round().astype(int)
    prior = rng.poisson(0.8, n).clip(0, 5)
    evidence = rng.integers(0, 6, n)
    attorney = (rng.random(n) < np.where(claim_type == "health", 0.12, 0.05)).astype(int)
    age_band = rng.choice(["18-34", "35-54", "55+"], n, p=[0.30, 0.45, 0.25])

    if drift_shift:
        # Simulates a regime change: slower reporting and higher amounts.
        days = (days * 1.9).round().astype(int)
        claimed = np.clip(claimed * 1.18, 300, 500_000)

    type_f = np.vectorize(TYPE_FACTOR.get)(claim_type)
    region_f = np.vectorize(REGION_FACTOR.get)(region)

    severity = claimed * type_f * region_f
    severity *= 1 + 0.003 * np.maximum(days - 7, 0)
    severity *= np.where(attorney == 1, 1.15, 1.0)
    severity *= 1 + 0.04 * prior
    severity *= 1 - 0.03 * evidence
    severity *= rng.lognormal(0, 0.25, n)
    severity = np.clip(severity, 1_000, 1_000_000).round(2)

    return pd.DataFrame({
        "claim_id": [f"HST-{i:05d}" for i in range(n)],
        "claim_type": claim_type,
        "region": region,
        "channel": channel,
        "claimed_amount": claimed.round(2),
        "days_to_report": days,
        "prior_claims": prior,
        "evidence_count": evidence,
        "attorney_involved": attorney,
        "age_band": age_band,          # recorded, deliberately NOT a model feature
        "severity": severity,
    })
