"""Tests for the public-demo guards and the metrics/dashboard endpoints.

These are the tests that make the deployment claim credible: a stranger cannot
upload a document, cannot flood the endpoint, and cannot spend past the cap.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from claims_assistant import config, demo
from claims_assistant.api import app


@pytest.fixture
def client():
    demo.reset_limiter()
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_demo(monkeypatch):
    demo.reset_limiter()
    yield
    demo.reset_limiter()


def _enable_demo(monkeypatch, rpm: int = 2, cap: float = 2.0, uploads: bool = False):
    monkeypatch.setattr(config, "DEMO_MODE", True)
    monkeypatch.setattr(config, "DEMO_RPM", rpm)
    monkeypatch.setattr(config, "DEMO_DAILY_USD", cap)
    monkeypatch.setattr(config, "DEMO_ALLOW_UPLOADS", uploads)
    demo.reset_limiter()


def test_demo_off_means_no_rate_limit(client, monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", False)
    for _ in range(5):
        assert client.post("/api/ask", json={"claim_id": "CLM-1041",
                                             "question": "limits?"}).status_code != 429


def test_rate_limit_blocks_then_reports_retry_after(client, monkeypatch):
    _enable_demo(monkeypatch, rpm=2)
    payload = {"claim_id": "CLM-1041", "question": "What is the rental limit?"}
    first = client.post("/api/ask", json=payload)
    second = client.post("/api/ask", json=payload)
    third = client.post("/api/ask", json=payload)

    assert first.status_code != 429
    assert second.status_code != 429
    assert third.status_code == 429
    assert "retry-after" in {k.lower() for k in third.headers}


def test_uploads_blocked_in_demo(client, monkeypatch):
    _enable_demo(monkeypatch, uploads=False)
    response = client.post(
        "/api/uploads",
        files={"file": ("claim.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 403
    assert "disabled" in response.json()["detail"].lower()


def test_spend_cap_blocks_paid_calls(client, monkeypatch):
    _enable_demo(monkeypatch, rpm=50, cap=1.0)
    monkeypatch.setattr(demo, "spent_usd", lambda: 1.5)
    response = client.post("/api/ask", json={"claim_id": "CLM-1041",
                                             "question": "limits?"})
    assert response.status_code == 429
    assert "budget" in response.json()["detail"].lower()


def test_health_reports_demo_state(client, monkeypatch):
    _enable_demo(monkeypatch, rpm=7, cap=3.0, uploads=False)
    body = client.get("/api/health").json()
    assert body["demo"]["enabled"] is True
    assert body["demo"]["rpm_per_ip"] == 7
    assert body["demo"]["uploads_enabled"] is False


def test_metrics_summary_shape(client):
    response = client.get("/api/metrics/summary")
    assert response.status_code == 200
    body = response.json()
    for key in ("headline", "eval", "usage", "queue", "demo", "generated_at"):
        assert key in body
    for key in ("golden_set_pass_rate", "p95_latency_ms", "cost_per_request_usd",
                "override_rate", "pending_reviews"):
        assert key in body["headline"]
    assert body["eval"]["cases"] >= 9


def test_dashboard_is_served(client):
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Claims Triage Console" in response.text
    assert "/api/metrics/summary" in response.text


def test_demo_status_math(monkeypatch):
    _enable_demo(monkeypatch, rpm=10, cap=2.0)
    monkeypatch.setattr(demo, "spent_usd", lambda: 0.75)
    status = demo.status()
    assert status["remaining_usd"] == pytest.approx(1.25)
    assert status["budget_exhausted"] is False


def test_limiter_refills_after_window():
    limiter = demo.SlidingWindowLimiter()
    assert limiter.check("ip", 2, window_seconds=60, now=100.0)[0]
    assert limiter.check("ip", 2, window_seconds=60, now=101.0)[0]
    allowed, retry = limiter.check("ip", 2, window_seconds=60, now=102.0)
    assert allowed is False
    assert retry == pytest.approx(58.0)
    # a full window later the bucket has drained
    assert limiter.check("ip", 2, window_seconds=60, now=161.0)[0]
