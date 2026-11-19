"""Shared test fixtures: FakeLLM so no API key or network is needed."""
from __future__ import annotations

import json

import pytest


class FakeLLM:
    """Scripted LLM: replays canned chat_tools steps and canned summaries."""

    def __init__(self, script: list[dict] | None = None,
                 chat_script: list[str] | None = None):
        self.script = list(script or [])
        self.chat_script = list(chat_script or [])
        self.calls = 0

    def chat_tools(self, messages, tools) -> dict:
        if not self.script:
            return {"content": "FAKE FINAL", "tool_calls": []}
        step = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return step

    def chat(self, system, user, json_mode=False, **kwargs) -> str:
        if self.chat_script:
            return self.chat_script.pop(0)
        return json.dumps({
            "incident": "fake incident",
            "key_facts": ["fact"],
            "evidence_present": ["evidence"],
            "gaps_or_inconsistencies": [],
            "plain_language": "fake plain language",
        })

    def summarize_claim(self, claim) -> dict:
        return {"incident": f"summary of {claim['claim_id']}",
                "key_facts": [], "evidence_present": [],
                "gaps_or_inconsistencies": [], "plain_language": "summary",
                "source_claim_id": claim["claim_id"]}

    def chat_vision(self, system, text, images, **kwargs) -> str:
        """Scripted vision response for OCR-path tests."""
        if self.chat_script:
            return self.chat_script.pop(0)
        return json.dumps({
            "incident": "fake incident", "key_facts": ["fact"],
            "evidence_present": ["evidence"], "gaps_or_inconsistencies": [],
            "plain_language": "fake plain language",
        })

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Deterministic keyword-count embedding for tests (3 dims)."""
        vectors = []
        for t in texts:
            low = t.lower()
            vec = [float(low.count("rental")), float(low.count("deadline")),
                   float(low.count("flood"))]
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            vectors.append([v / norm for v in vec])
        return vectors


@pytest.fixture
def fake_llm():
    return FakeLLM()
