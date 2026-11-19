"""Severity model: training quality, prediction shape, protected attributes."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claims_assistant.severity import (FEATURES, PROTECTED_ATTRIBUTES,  # noqa: E402
                                       predict_claim, report, train)

CLAIM = {
    "claim_id": "CLM-1041", "claim_type": "auto", "region": "CA",
    "claimed_amount": 8420.0, "incident_date": "2026-09-12",
    "reported_date": "2026-09-13", "verbatim": "I took photos at the scene.",
}


def test_train_and_report(tmp_path):
    rep = train(n=500, seed=1, model_dir=tmp_path)
    m = rep["metrics"]
    assert m["mae"] > 0
    assert m["baseline_mae"] > m["mae"], "model must beat the mean baseline"
    assert m["r2"] > 0.5
    assert len(rep["clusters"]) == 4
    assert rep["top_features"], "SHAP/importance must be produced"
    assert (tmp_path / "severity.joblib").exists()
    assert (tmp_path / "drift_baseline.json").exists()


def test_report_roundtrip(tmp_path):
    train(n=400, seed=1, model_dir=tmp_path)
    rep = report(model_dir=tmp_path)
    assert rep and rep["metrics"]["rows"] == 400


def test_predict_claim_shape(tmp_path):
    train(n=500, seed=1, model_dir=tmp_path)
    pred = predict_claim(CLAIM, model_dir=tmp_path)
    assert pred is not None
    assert pred["predicted_severity"] > 0
    assert 0 <= pred["cluster"] < 4
    assert pred["cluster_label"] in {"high-severity", "elevated", "typical", "low-severity"}
    assert pred["drivers"], "per-claim SHAP drivers expected"


def test_protected_attributes_not_features():
    for attr in PROTECTED_ATTRIBUTES:
        assert attr not in FEATURES
    assert "age_band" in PROTECTED_ATTRIBUTES


def test_predict_without_model_returns_none(tmp_path):
    assert predict_claim(CLAIM, model_dir=tmp_path) is None
