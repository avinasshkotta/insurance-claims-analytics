"""Token and cost accounting for every LLM call.

LLMOps step 5: log model id, input/output tokens, and inferred cost per call so
cost can be attributed and alerted on. Written as JSONL for simple querying.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import config

# USD per 1M tokens: (input, output). Override with MODEL_PRICE_IN/OUT env vars.
DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "text-embedding-3-small": (0.02, 0.0),
}
_FALLBACK = (0.50, 1.50)


def price_for(model: str) -> tuple[float, float]:
    return DEFAULT_PRICES.get(model, _FALLBACK)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    inp, out = price_for(model)
    return round((input_tokens * inp + output_tokens * out) / 1_000_000, 8)


def record(kind: str, model: str, input_tokens: int, output_tokens: int,
           meta: dict | None = None, path: Path | None = None) -> dict:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": kind,
        "model": model,
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "cost_usd": estimate_cost(model, input_tokens or 0, output_tokens or 0),
    }
    if meta:
        entry["meta"] = meta
    target = path or config.USAGE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    return entry


def summary(path: Path | None = None) -> dict:
    target = path or config.USAGE_PATH
    calls = input_tokens = output_tokens = 0
    cost = 0.0
    by_kind: dict[str, dict] = {}
    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            calls += 1
            input_tokens += e.get("input_tokens", 0)
            output_tokens += e.get("output_tokens", 0)
            cost += e.get("cost_usd", 0.0)
            k = e.get("kind", "unknown")
            bucket = by_kind.setdefault(k, {"calls": 0, "input_tokens": 0,
                                            "output_tokens": 0, "cost_usd": 0.0})
            bucket["calls"] += 1
            bucket["input_tokens"] += e.get("input_tokens", 0)
            bucket["output_tokens"] += e.get("output_tokens", 0)
            bucket["cost_usd"] = round(bucket["cost_usd"] + e.get("cost_usd", 0.0), 8)
    return {"calls": calls, "input_tokens": input_tokens,
            "output_tokens": output_tokens, "cost_usd": round(cost, 8),
            "by_kind": by_kind}


def reset(path: Path | None = None) -> None:
    target = path or config.USAGE_PATH
    if target.exists():
        target.unlink()
