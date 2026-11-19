"""Prompt registry — versioned prompt files live in `prompts/` (git-versioned).

LLMOps step 1: prompts are artifacts, not inline strings. Each prompt carries a
declared version and a content digest, so an answer can be traced to the exact
prompt revision that produced it.
"""
from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path

from . import config

PROMPTS_DIR = config.PROJECT_ROOT / "prompts"


def _strip_header(text: str) -> tuple[str, dict]:
    """Strip the `> key: value` metadata block at the top of a prompt file."""
    meta: dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines) and (lines[i].startswith(">") or not lines[i].strip()):
        line = lines[i].lstrip("> ").strip()
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
        i += 1
    return "\n".join(lines[i:]).strip(), meta


@lru_cache(maxsize=None)
def _parse(path_str: str, mtime_ns: int) -> dict:
    """Parse a prompt file; the mtime key makes edits hot-reload without a restart."""
    path = Path(path_str)
    raw = path.read_text(encoding="utf-8")
    body, meta = _strip_header(raw)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:10]
    declared = meta.get("version", "0.0.0")
    return {
        "name": path.stem,
        "text": body,
        "version": f"{declared}+{digest}",
        "digest": digest,
        "declared_version": declared,
        "path": str(path),
    }


def load_prompt(name: str) -> dict:
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt not found: {path}")
    return _parse(str(path), path.stat().st_mtime_ns)


def prompt_text(name: str) -> str:
    return load_prompt(name)["text"]


def registry() -> dict[str, str]:
    """name -> version, for health/debug endpoints."""
    out = {}
    for path in sorted(PROMPTS_DIR.glob("*.md")):
        try:
            out[path.stem] = load_prompt(path.stem)["version"]
        except Exception:  # pragma: no cover - defensive
            out[path.stem] = "unreadable"
    return out


_VERSION_IN_TEXT = re.compile(r"^> version:", re.MULTILINE)
