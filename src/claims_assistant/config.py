"""Configuration: paths and environment."""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:  # dotenv optional; env vars may be set directly
    pass

# Project layout
PKG_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PKG_DIR.parents[1]
# Project layout
PKG_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PKG_DIR.parents[1]
DATA_DIR = PROJECT_ROOT / "data"

# Read-only inputs shipped in the image.
POLICY_DIR = DATA_DIR / "policies"
EMBEDDINGS_PATH = DATA_DIR / "embeddings.json"

# Writable location. On a read-only filesystem (AWS Lambda) set WRITE_DIR=/tmp/data:
# runtime state lands there and is reseeded from the packaged data on cold start.
_write_env = os.getenv("WRITE_DIR", "").strip()
WRITE_DIR = Path(_write_env).expanduser() if _write_env else None
if WRITE_DIR is not None:
    WRITE_DIR.mkdir(parents=True, exist_ok=True)
    _seed = DATA_DIR / "claims.json"
    _target = WRITE_DIR / "claims.json"
    if _seed.exists() and not _target.exists():
        import shutil

        shutil.copyfile(_seed, _target)

_WRITABLE = WRITE_DIR or DATA_DIR
CLAIMS_PATH = _WRITABLE / "claims.json"
CASE_QUEUE_PATH = _WRITABLE / "case_queue.json"
UPLOADS_DIR = _WRITABLE / "uploads"
USAGE_PATH = _WRITABLE / "llm_usage.jsonl"
RUNS_PATH = _WRITABLE / "runs.jsonl"
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EVAL_REPORT_PATH = (WRITE_DIR or (PROJECT_ROOT / "docs")) / "eval_report.json"

# LLM (Layer 1) — any OpenAI-compatible endpoint
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL") or None
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

# Triage (Layer 3) — review-capacity knob, no retraining required
RISK_THRESHOLD = float(os.getenv("RISK_THRESHOLD", "0.5"))

# MCP transport trust boundary (set a real secret in .env for shared use)
MCP_AUTH_TOKEN = os.getenv("MCP_AUTH_TOKEN", "local-demo-token")

# ---------------------------------------------------------------------------
# Deployment / public-demo guardrails
# ---------------------------------------------------------------------------
_TRUE = {"1", "true", "yes", "on"}


def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in _TRUE


# DEMO_MODE turns on the guards in demo.py: per-IP rate limiting, a hard daily
# spend cap, uploads off, and a shorter output-token ceiling.
DEMO_MODE = _flag("DEMO_MODE")
DEMO_RPM = int(os.getenv("DEMO_RPM", "12"))
DEMO_DAILY_USD = float(os.getenv("DEMO_DAILY_USD", "2.00"))
DEMO_ALLOW_UPLOADS = _flag("DEMO_ALLOW_UPLOADS")

# Applies in every mode: a ceiling on generation length is the cheapest cost
# control that exists, and 700 tokens is enough for every structured output here.
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "700"))
