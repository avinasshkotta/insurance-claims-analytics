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
DATA_DIR = PROJECT_ROOT / "data"
CLAIMS_PATH = DATA_DIR / "claims.json"
POLICY_DIR = DATA_DIR / "policies"
CASE_QUEUE_PATH = DATA_DIR / "case_queue.json"
USAGE_PATH = DATA_DIR / "llm_usage.jsonl"
RUNS_PATH = DATA_DIR / "runs.jsonl"
EMBEDDINGS_PATH = DATA_DIR / "embeddings.json"
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EVAL_REPORT_PATH = PROJECT_ROOT / "docs" / "eval_report.json"

# LLM (Layer 1) — any OpenAI-compatible endpoint
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL") or None
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

# Triage (Layer 3) — review-capacity knob, no retraining required
RISK_THRESHOLD = float(os.getenv("RISK_THRESHOLD", "0.5"))

# MCP transport trust boundary (set a real secret in .env for shared use)
MCP_AUTH_TOKEN = os.getenv("MCP_AUTH_TOKEN", "local-demo-token")
