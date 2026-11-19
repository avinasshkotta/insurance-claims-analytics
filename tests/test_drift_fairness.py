"""Drift (PSI) and segment parity reports."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claims_assistant.drift import drift_report  # noqa: E402
from claims_assistant.fairness import segment_report  # noqa: E402
from claims_assistant.severity import train  # noqa: E402


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    path = tmp_path_factory.mktemp("models")
    train(n=500, seed=2, model_dir=path)
    return path


def test_drift_flags_shifted_feature(trained):
    rep = drift_report(model_dir=trained)
    assert rep["features"]["days_to_report"]["status"] == "drift"
    assert rep["verdict"] == "retrain"
    assert rep["features"]["prior_claims"]["status"] == "stable"


def test_drift_never_negative(trained):
    rep = drift_report(model_dir=trained)
    assert all(v["psi"] >= 0 for v in rep["features"].values())


def test_fairness_report_shape(trained):
    rep = segment_report(model_dir=trained, n=400)
    assert rep["selection_rate_overall"] == pytest.approx(0.25, abs=0.05)
    assert set(rep["by_region"]) <= {"CA", "TX", "FL", "NY"}
    assert 0 <= rep["gap_region"] <= 1
    assert "age_band" in rep["protected_attributes_excluded_from_features"]
    assert "age_band" not in rep["features_used"]


def test_fairness_requires_model(tmp_path):
    with pytest.raises(FileNotFoundError):
        segment_report(model_dir=tmp_path)


def test_drift_requires_baseline(tmp_path):
    with pytest.raises(FileNotFoundError):
        drift_report(model_dir=tmp_path)
