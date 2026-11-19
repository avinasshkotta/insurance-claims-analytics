"""Persisted run traces — audit trail for every agent run (Production Ops).

Each run records: what was asked, which tools were called with which args,
what the model returned, prompt versions, model, and timestamp. This is the
debugging trail for the 'difficult to debug' agent failure mode.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .prompts import registry


def record_run(kind: str, claim_ids: list[str], tool_log: list[dict],
               final: str, model: str | None = None,
               path: Path | None = None) -> dict:
    entry = {
        "run_id": "run_" + uuid.uuid4().hex[:10],
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": kind,                      # triage | ask | batch | cli
        "claim_ids": claim_ids,
        "model": model,
        "prompts": registry(),
        "tool_calls": len(tool_log),
        "tools": [t.get("tool") for t in tool_log],
        "tool_errors": sum(1 for t in tool_log if not t.get("ok", True)),
        "final": final,
    }
    target = path or config.RUNS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    return entry


def list_runs(limit: int = 20, path: Path | None = None) -> list[dict]:
    target = path or config.RUNS_PATH
    if not target.exists():
        return []
    entries = []
    for line in target.read_text(encoding="utf-8").splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries[-limit:][::-1]
