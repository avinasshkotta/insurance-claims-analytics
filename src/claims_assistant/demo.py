"""Demo-mode guardrails for a public deployment.

A public demo runs on *your* API key, so three things must be true before anyone
else can click it:

1. a stranger cannot run up a bill (per-IP rate limit + hard daily spend cap)
2. a stranger cannot upload documents (PII enters your logs and storage)
3. every LLM call is capped (max output tokens)

None of this is security theatre: each guard is testable, and tests/test_demo.py
proves the cap actually blocks.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

from fastapi import HTTPException, Request

from . import config, usage

# Endpoints that cost money or touch uploaded files.
PAID_PREFIXES = ("/api/ask", "/api/triage", "/api/uploads", "/api/reindex")


@dataclass(frozen=True)
class DemoLimits:
    rpm: int
    daily_usd: float
    allow_uploads: bool


def limits() -> DemoLimits:
    return DemoLimits(
        rpm=max(1, config.DEMO_RPM),
        daily_usd=max(0.0, config.DEMO_DAILY_USD),
        allow_uploads=config.DEMO_ALLOW_UPLOADS,
    )


class SlidingWindowLimiter:
    """Per-key sliding window. In-memory: correct for one replica only."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(
        self, key: str, limit: int, window_seconds: float = 60.0, now: float | None = None
    ) -> tuple[bool, float]:
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                return False, max(0.0, window_seconds - (now - hits[0]))
            hits.append(now)
            return True, 0.0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


_limiter = SlidingWindowLimiter()


def reset_limiter() -> None:
    _limiter.reset()


def client_ip(request: Request) -> str:
    """Behind a proxy (HF Spaces, Cloud Run) the client IP is in X-Forwarded-For."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def is_paid_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in PAID_PREFIXES)


def spent_usd() -> float:
    try:
        return float(usage.summary().get("cost_usd", 0.0))
    except Exception:  # accounting must never take the app down
        return 0.0


def status() -> dict:
    lim = limits()
    spent = spent_usd()
    return {
        "enabled": bool(config.DEMO_MODE),
        "rpm_per_ip": lim.rpm,
        "daily_usd_cap": lim.daily_usd,
        "spent_usd": round(spent, 6),
        "remaining_usd": round(max(0.0, lim.daily_usd - spent), 6),
        "uploads_enabled": lim.allow_uploads,
        "budget_exhausted": spent >= lim.daily_usd,
        "max_output_tokens": config.MAX_OUTPUT_TOKENS,
    }


def check_request(request: Request) -> None:
    """Raise HTTPException instead of serving a request the demo cannot afford."""
    if not config.DEMO_MODE:
        return
    if not request.method == "POST" or not is_paid_path(request.url.path):
        return

    lim = limits()

    if request.url.path.startswith("/api/uploads") and not lim.allow_uploads:
        raise HTTPException(
            status_code=403,
            detail="Document upload is disabled in this public demo. "
                   "Run the project locally to try the ingestion pipeline.",
        )

    if spent_usd() >= lim.daily_usd:
        raise HTTPException(
            status_code=429,
            detail="This demo has reached its daily budget. "
                   "It resets in a few hours, or run the project locally.",
        )

    allowed, retry_after = _limiter.check(client_ip(request), lim.rpm)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit: {lim.rpm} requests per minute per visitor.",
            headers={"Retry-After": str(max(1, int(retry_after) + 1))},
        )
