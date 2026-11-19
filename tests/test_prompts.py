"""Prompt registry: versioned files load, strip metadata, keep placeholders."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claims_assistant.prompts import load_prompt, prompt_text, registry  # noqa: E402


def test_all_prompts_load():
    reg = registry()
    assert {"summary", "rag_answer", "triage_system"} <= set(reg), reg
    for name in ("summary", "rag_answer", "triage_system"):
        p = load_prompt(name)
        assert p["text"], f"{name} has no body"
        assert not p["text"].startswith(">"), f"{name} metadata header not stripped"
        assert p["digest"] and p["version"].startswith(("1.", "0."))


def test_summary_prompt_placeholders():
    text = prompt_text("summary")
    assert "{meta}" in text
    assert "{verbatim}" in text
    assert '"incident"' in text  # JSON example intact


def test_triage_prompt_threshold_placeholder():
    assert "{threshold}" in prompt_text("triage_system")


def test_rag_prompt_has_refusal_rule():
    text = prompt_text("rag_answer")
    assert "INSUFFICIENT RETRIEVED CONTEXT" in text
    assert "Never use outside insurance knowledge" in text


def test_version_changes_with_content(tmp_path, monkeypatch):
    import claims_assistant.prompts as pmod

    (tmp_path / "demo.md").write_text("> version: 2.0.0\nHello world", encoding="utf-8")
    monkeypatch.setattr(pmod, "PROMPTS_DIR", tmp_path)
    first = pmod.load_prompt("demo")["version"]
    (tmp_path / "demo.md").write_text("> version: 2.0.0\nHello world!", encoding="utf-8")
    second = pmod.load_prompt("demo")["version"]
    assert first != second, "content change must change the version digest"
