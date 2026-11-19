"""Expected-cost regression, anomaly clustering, and SHAP explanations.

This is the RCTC's model layer: predict claim severity, group unusual claims into
interpretable clusters, and explain both. Synthetic labels today; swap in real
adjudicated history and the pipeline is unchanged.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from . import config
from .ml_data import generate_claims

NUMERIC = ["claimed_amount", "days_to_report", "prior_claims",
           "evidence_count", "attorney_involved"]
CATEGORICAL = ["claim_type", "region", "channel"]
FEATURES = NUMERIC + CATEGORICAL

PROTECTED_ATTRIBUTES = ["age_band", "gender", "ethnicity", "race", "religion"]
MODEL_DIR = config.PROJECT_ROOT / "models"
BUNDLE_PATH = MODEL_DIR / "severity.joblib"
BASELINE_PATH = MODEL_DIR / "drift_baseline.json"


def _preprocessor() -> ColumnTransformer:
    return ColumnTransformer([
        ("num", StandardScaler(), NUMERIC),
        ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL),
    ])


def _feature_names(pre: ColumnTransformer) -> list[str]:
    names = list(NUMERIC)
    try:
        names += list(pre.named_transformers_["cat"].get_feature_names_out(CATEGORICAL))
    except Exception:  # pragma: no cover - older sklearn
        names += CATEGORICAL
    return names


def _psi_numeric(base: np.ndarray, actual: np.ndarray, bins: int = 10) -> float:
    edges = np.unique(np.quantile(base, np.linspace(0, 1, bins + 1)))
    if len(edges) < 2:
        return 0.0
    base_counts, _ = np.histogram(base, bins=edges)
    act_counts, _ = np.histogram(actual, bins=edges)
    b = np.clip(base_counts / max(base_counts.sum(), 1), 1e-6, None)
    a = np.clip(act_counts / max(act_counts.sum(), 1), 1e-6, None)
    return float(np.sum((a - b) * np.log(a / b)))


def _drift_baseline(df: pd.DataFrame) -> dict:
    return {
        "numeric": {c: {"values": df[c].astype(float).tolist()} for c in NUMERIC},
        "categorical": {
            c: {str(k): float(v) for k, v in df[c].value_counts(normalize=True).items()}
            for c in CATEGORICAL
        },
    }


def train(n: int = 2500, seed: int = 42, model_dir: Path | None = None,
          data: pd.DataFrame | None = None) -> dict:
    """Train severity regressor + clusters; persist artifacts; return report."""
    out_dir = model_dir or MODEL_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    df = data if data is not None else generate_claims(n, seed)
    X, y = df[FEATURES], df["severity"]
    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, random_state=seed)

    pre = _preprocessor().fit(X_tr)
    X_tr_t, X_te_t = pre.transform(X_tr), pre.transform(X_te)

    gbr = GradientBoostingRegressor(
        n_estimators=200, max_depth=3, learning_rate=0.05, random_state=seed)
    gbr.fit(X_tr_t, y_tr)
    pred = gbr.predict(X_te_t)

    baseline = DummyRegressor(strategy="mean").fit(X_tr_t, y_tr)
    base_pred = baseline.predict(X_te_t)

    metrics = {
        "mae": round(float(mean_absolute_error(y_te, pred)), 2),
        "rmse": round(float(mean_squared_error(y_te, pred) ** 0.5), 2),
        "r2": round(float(r2_score(y_te, pred)), 4),
        "baseline_mae": round(float(mean_absolute_error(y_te, base_pred)), 2),
        "rows": int(len(df)),
        "test_rows": int(len(X_te)),
    }

    km = KMeans(n_clusters=4, n_init=10, random_state=seed).fit(X_tr_t)
    labels = km.predict(X_tr_t)
    profiles = []
    for c in range(4):
        mask = labels == c
        profiles.append({
            "cluster": c,
            "n": int(mask.sum()),
            "mean_severity": round(float(y_tr[mask].mean()), 2),
            "mean_days_to_report": round(float(X_tr[mask]["days_to_report"].mean()), 1),
            "attorney_rate": round(float(X_tr[mask]["attorney_involved"].mean()), 3),
            "mean_evidence": round(float(X_tr[mask]["evidence_count"].mean()), 2),
        })
    profiles.sort(key=lambda p: -p["mean_severity"])
    for rank, p in enumerate(profiles):
        p["label"] = ["high-severity", "elevated", "typical", "low-severity"][rank]

    feature_names = _feature_names(pre)
    importance: dict[str, float] = {}
    try:
        import shap
        explainer = shap.TreeExplainer(gbr)
        values = explainer.shap_values(X_te_t[:200])
        importance = {
            name: round(float(np.abs(values[:, i]).mean()), 2)
            for i, name in enumerate(feature_names)
        }
        top_features = sorted(importance, key=lambda k: -importance[k])[:8]
    except Exception:  # pragma: no cover - shap optional at runtime
        top_features = []
        importance = {n: round(float(v), 4)
                      for n, v in zip(feature_names, gbr.feature_importances_)}

    report = {
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "metrics": metrics,
        "clusters": profiles,
        "shap_importance": importance,
        "top_features": top_features,
        "features": FEATURES,
        "excluded_attributes": PROTECTED_ATTRIBUTES,
    }
    bundle = {"pre": pre, "gbr": gbr, "km": km, "feature_names": feature_names,
              "report": report}
    joblib.dump(bundle, out_dir / "severity.joblib")
    (out_dir / "drift_baseline.json").write_text(
        json.dumps(_drift_baseline(df[NUMERIC + CATEGORICAL].sample(
            min(len(df), 1500), random_state=seed))), encoding="utf-8")
    return report


def load(model_dir: Path | None = None) -> dict | None:
    path = (model_dir or MODEL_DIR) / "severity.joblib"
    if not path.exists():
        return None
    try:
        return _load_cached(str(path), path.stat().st_mtime_ns)
    except Exception:
        # Incompatible pickle (e.g. sklearn version drift): treat as untrained.
        return None


@lru_cache(maxsize=4)
def _load_cached(path_str: str, mtime_ns: int) -> dict:
    return joblib.load(path_str)


def _claim_row(claim: dict) -> dict:
    def _days() -> int:
        try:
            inc = date.fromisoformat(claim.get("incident_date", ""))
            rep = date.fromisoformat(claim.get("reported_date", ""))
            return max((rep - inc).days, 0)
        except ValueError:
            return 0

    verbatim = claim.get("verbatim", "").lower()
    evidence = sum(k in verbatim for k in
                   ("photo", "invoice", "receipt", "report", "document", "estimate"))
    attorney = int(bool(__import__("re").search(r"attorney|lawyer|law firm", verbatim)))
    prior = sum(ord(ch) for ch in claim.get("claim_id", "")) % 3
    return {
        "claimed_amount": float(claim.get("claimed_amount") or 0),
        "days_to_report": _days(),
        "prior_claims": prior,
        "evidence_count": evidence,
        "attorney_involved": attorney,
        "claim_type": claim.get("claim_type", "auto"),
        "region": claim.get("region", "CA"),
        "channel": claim.get("channel", "phone"),
    }


def predict_claim(claim: dict, model_dir: Path | None = None) -> dict | None:
    bundle = load(model_dir)
    if bundle is None:
        return None
    row = _claim_row(claim)
    X = pd.DataFrame([row])[FEATURES]
    Xt = bundle["pre"].transform(X)
    predicted = float(bundle["gbr"].predict(Xt)[0])
    cluster = int(bundle["km"].predict(Xt)[0])
    profile = next((p for p in bundle["report"]["clusters"]
                    if p["cluster"] == cluster), None)

    drivers = []
    try:
        import shap
        values = shap.TreeExplainer(bundle["gbr"]).shap_values(Xt)[0]
        order = np.argsort(-np.abs(values))[:3]
        drivers = [{"feature": bundle["feature_names"][i],
                    "impact": round(float(values[i]), 0)} for i in order]
    except Exception:  # pragma: no cover
        pass

    return {
        "predicted_severity": round(predicted, 2),
        "cluster": cluster,
        "cluster_label": (profile or {}).get("label"),
        "drivers": drivers,
        "model_mae": bundle["report"]["metrics"]["mae"],
    }


def report(model_dir: Path | None = None) -> dict | None:
    bundle = load(model_dir)
    return bundle["report"] if bundle else None
