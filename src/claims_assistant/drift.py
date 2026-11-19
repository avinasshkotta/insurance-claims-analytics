"""Drift monitoring with PSI (Population Stability Index).

Evidently-style check without the dependency: compare each feature's training
distribution against a recent sample; PSI < 0.1 stable, 0.1–0.2 monitor,
> 0.2 drift (retraining trigger in MLOps terms).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .ml_data import generate_claims
from .severity import BASELINE_PATH, CATEGORICAL, NUMERIC, _psi_numeric

THRESHOLDS = {"stable": 0.1, "monitor": 0.2}


def _psi_categorical(base: dict[str, float], actual: pd.Series) -> float:
    total = max(len(actual), 1)
    values = set(base) | set(actual.astype(str).unique())
    psi = 0.0
    for value in values:
        expected = max(float(base.get(value, 0.0)), 1e-6)
        observed = max(float((actual.astype(str) == value).sum()) / total, 1e-6)
        psi += (observed - expected) * np.log(observed / expected)
    return float(psi)


def _status(psi: float) -> str:
    if psi < THRESHOLDS["stable"]:
        return "stable"
    if psi < THRESHOLDS["monitor"]:
        return "monitor"
    return "drift"


def drift_report(model_dir: Path | None = None,
                 recent: pd.DataFrame | None = None) -> dict:
    baseline_path = (model_dir or BASELINE_PATH.parent) / "drift_baseline.json"
    if not baseline_path.exists():
        raise FileNotFoundError("drift baseline missing — run `cli ml` first")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    recent = recent if recent is not None else generate_claims(
        1000, seed=7, drift_shift=True)

    features: dict[str, dict] = {}
    for name in NUMERIC:
        base_values = np.array(baseline["numeric"][name]["values"], dtype=float)
        psi = _psi_numeric(base_values, recent[name].astype(float).to_numpy())
        features[name] = {"psi": round(psi, 4), "type": "numeric",
                          "status": _status(psi)}
    for name in CATEGORICAL:
        psi = _psi_categorical(baseline["categorical"][name], recent[name])
        features[name] = {"psi": round(psi, 4), "type": "categorical",
                          "status": _status(psi)}

    drifted = [f for f, v in features.items() if v["status"] == "drift"]
    monitored = [f for f, v in features.items() if v["status"] == "monitor"]
    return {
        "recent_rows": int(len(recent)),
        "features": features,
        "drifted": drifted,
        "monitoring": monitored,
        "verdict": "retrain" if drifted else
                   ("watch" if monitored else "stable"),
    }
