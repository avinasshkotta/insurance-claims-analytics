"""Segment parity audit for triage outcomes.

Claims have no protected attributes in this corpus by design; the audit does two
jobs: (1) proves protected attributes are excluded from model features, and
(2) checks outcome-rate parity across workflow segments (claim type, region,
channel) so a review-capacity threshold doesn't silently skew by segment.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .ml_data import generate_claims
from .severity import FEATURES, PROTECTED_ATTRIBUTES, load


def _rates(df: pd.DataFrame, column: str) -> dict:
    grouped = df.groupby(column)["flagged"].mean().sort_values(ascending=False)
    return {str(k): round(float(v), 4) for k, v in grouped.items()}


def segment_report(model_dir: Path | None = None, n: int = 1500,
                   seed: int = 99, quantile: float = 0.75) -> dict:
    bundle = load(model_dir)
    if bundle is None:
        raise FileNotFoundError("model missing — run `cli ml` first")
    df = generate_claims(n, seed)
    preds = bundle["gbr"].predict(bundle["pre"].transform(df[FEATURES]))
    cutoff = float(np.quantile(preds, quantile))
    df = df.assign(flagged=preds >= cutoff)

    region = _rates(df, "region")
    claim_type = _rates(df, "claim_type")
    channel = _rates(df, "channel")

    def gap(rates: dict) -> float:
        return round(max(rates.values()) - min(rates.values()), 4) if rates else 0.0

    return {
        "rows": int(len(df)),
        "cutoff_severity": round(cutoff, 2),
        "selection_rate_overall": round(float(df["flagged"].mean()), 4),
        "by_region": region,
        "by_claim_type": claim_type,
        "by_channel": channel,
        "gap_region": gap(region),
        "gap_claim_type": gap(claim_type),
        "gap_channel": gap(channel),
        "features_used": FEATURES,
        "protected_attributes_recorded": [c for c in PROTECTED_ATTRIBUTES if c in df],
        "protected_attributes_excluded_from_features": [
            c for c in PROTECTED_ATTRIBUTES if c in df and c not in FEATURES],
        "note": "claim_type/region are rating segments, not protected classes.",
    }
