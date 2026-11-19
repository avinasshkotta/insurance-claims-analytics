"""Guardrails: input validation, PII hygiene, and output citation checks.

Production Ops baseline from the reference material: treat every user message and
retrieved document as untrusted input to the LLM — the prompt-injection analogue
of SQL injection defence. Also verifies that citations in an answer actually
exist in the retrieved set.
"""
from __future__ import annotations

import re

# Prompt-injection / instruction-hijack patterns. Deliberately transparent and
# testable — a blocklist is a speed bump, not a complete defence.
INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("ignore_previous", re.compile(
        r"ignore\s+(all\s+)?(the\s+)?(previous|prior|above)\s+instructions", re.I)),
    ("disregard_system", re.compile(
        r"disregard\s+(the\s+)?(system|previous|prior|safety)", re.I)),
    ("reveal_prompt", re.compile(
        r"(reveal|show|print|repeat)\s+(me\s+)?(your|the)\s+"
        r"(system\s+)?(prompt|instructions)", re.I)),
    ("role_hijack", re.compile(r"\byou\s+are\s+now\b|\bact\s+as\s+(a|an)\b", re.I)),
    ("jailbreak", re.compile(r"\bjailbreak\b|\bdeveloper\s+mode\b", re.I)),
    ("override_safety", re.compile(
        r"override\s+(the\s+)?(system|safety|guardrails?)", re.I)),
]

PII_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "***-**-****"),
    ("phone", re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"), "***-***-****"),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "***@***"),
]

CITATION_RE = re.compile(r"\[([A-Z][A-Z0-9-]+)\s*·\s*([^\]]+)\]")


def scan_input(text: str) -> dict:
    """Return injection findings for a user-supplied string."""
    hits = [name for name, pattern in INJECTION_PATTERNS if pattern.search(text)]
    return {
        "blocked": bool(hits),
        "score": 1.0 if hits else 0.0,
        "matches": hits,
    }


def redact_pii(text: str) -> tuple[str, list[str]]:
    """Mask PII in free text (policy questions are user input too)."""
    found: list[str] = []
    for name, pattern, mask in PII_PATTERNS:
        if pattern.search(text):
            found.append(name)
            text = pattern.sub(mask, text)
    return text, found


def check_output(answer: str, retrieved_pairs: set[tuple[str, str]]) -> dict:
    """Verify every citation in an answer exists in the retrieved set.

    retrieved_pairs is {(doc_id, section), ...}. Returns invalid citations and
    a pass flag — used to catch fabricated sources.
    """
    cited = {(doc.strip(), section.strip()) for doc, section in CITATION_RE.findall(answer)}
    invalid = sorted(f"{doc} · {section}" for doc, section in cited
                     if (doc, section) not in retrieved_pairs)
    return {"ok": not invalid, "cited": sorted(f"{d} · {s}" for d, s in cited),
            "invalid": invalid}


def sanitize_retrieved(chunks: list[dict]) -> tuple[list[dict], list[str]]:
    """Drop retrieved chunks that contain instruction-hijack patterns.

    Retrieved documents are untrusted input: a poisoned policy file must not be
    able to redirect the agent.
    """
    clean, dropped = [], []
    for c in chunks:
        if scan_input(c.get("text", ""))["blocked"]:
            dropped.append(f"{c.get('doc_id')} · {c.get('section')}")
        else:
            clean.append(c)
    return clean, dropped
