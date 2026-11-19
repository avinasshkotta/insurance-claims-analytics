"""Layer 1 — LLM: plain-language claim verbatim summarization.

Constraint (from RCTC): summarize, never judge; redact PII; stay verbatim-faithful;
fixed output structure so downstream layers can parse it.

Prompts are loaded from `prompts/summary.md` (versioned in git), and every call
is metered through `usage.py`.
"""
from __future__ import annotations

import base64
import json
import re

from . import config, usage
from .prompts import prompt_text


def _redact_pii(text: str) -> str:
    """Mask common PII patterns before they reach the model or the output."""
    text = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "***-**-****", text)  # SSN first (3-2-4)
    text = re.sub(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b",
                  "***-***-****", text)  # phone (3-3-4)
    text = re.sub(r"\b\d{9,}\b", lambda m: m.group(0)[:3] + "*" * (len(m.group(0)) - 3), text)
    text = re.sub(
        r"\b[A-Z]{1,2}-?\d{4,}\b",
        lambda m: m.group(0)[:2] + "*" * (len(m.group(0)) - 2),
        text,
    )  # plate-like tokens
    return text


def _summary_prompt(claim: dict) -> str:
    """Build the Layer-1 prompt from the versioned template.

    Uses plain string replacement, NOT str.format(): the prompt contains a
    literal JSON example whose braces would be interpreted as format fields.
    """
    meta = json.dumps({k: claim.get(k) for k in
                       ("claim_id", "claim_type", "region",
                        "incident_date", "reported_date")})
    verbatim = _redact_pii(claim.get("verbatim", ""))
    return prompt_text("summary").replace("{meta}", meta).replace("{verbatim}", verbatim)


class LLMClient:
    """Thin wrapper over an OpenAI-compatible chat client."""

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 base_url: str | None = None):
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("pip install openai") from exc
        self.model = model or config.LLM_MODEL
        self._client = OpenAI(
            api_key=api_key or config.OPENAI_API_KEY or "missing-key",
            base_url=base_url or config.OPENAI_BASE_URL,
        )

    def _meter(self, kind: str, response) -> None:
        """Log token usage/cost; never let accounting break the call path."""
        try:
            u = getattr(response, "usage", None)
            usage.record(kind, self.model,
                         getattr(u, "prompt_tokens", 0) or 0,
                         getattr(u, "completion_tokens", 0) or 0)
        except Exception:  # pragma: no cover - accounting is best-effort
            pass

    def chat(self, system: str, user: str, *, json_mode: bool = False,
             kind: str = "chat") -> str:
        kwargs = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=0,
            **kwargs,
        )
        self._meter(kind, resp)
        return (resp.choices[0].message.content or "").strip()

    def chat_tools(self, messages: list[dict], tools: list[dict]) -> dict:
        """One tool-calling turn. Returns {content, tool_calls:[{id,name,arguments}]}."""
        resp = self._client.chat.completions.create(
            model=self.model, messages=messages, tools=tools, temperature=0)
        self._meter("tools", resp)
        msg = resp.choices[0].message
        calls = []
        for tc in (msg.tool_calls or []):
            raw = tc.function.arguments or "{}"
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                args = {"_raw": raw}
            calls.append({"id": tc.id, "name": tc.function.name, "arguments": args})
        return {"content": msg.content, "tool_calls": calls}

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts with the configured embedding model (metered)."""
        resp = self._client.embeddings.create(model=config.EMBEDDING_MODEL, input=texts)
        try:
            u = getattr(resp, "usage", None)
            usage.record("embedding", config.EMBEDDING_MODEL,
                         getattr(u, "total_tokens", 0) or 0, 0)
        except Exception:  # pragma: no cover
            pass
        return [d.embedding for d in resp.data]

    def chat_vision(self, system: str, text: str, images: list[bytes],
                    *, kind: str = "vision") -> str:
        """Vision call for scanned documents/images (OCR via the model)."""
        content: list[dict] = [{"type": "text", "text": text}]
        for image in images:
            b64 = base64.b64encode(image).decode("ascii")
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"}})
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": content}],
            temperature=0,
        )
        self._meter(kind, resp)
        return (resp.choices[0].message.content or "").strip()

    # ---- Layer 1 capability -------------------------------------------------
    def summarize_claim(self, claim: dict) -> dict:
        """Return the structured plain-language summary for a claim verbatim."""
        raw = self.chat(
            "You are a precise JSON-producing assistant.",
            _summary_prompt(claim),
            json_mode=True,
            kind="summarize",
        )
        try:
            summary = json.loads(raw)
        except json.JSONDecodeError:
            summary = {"incident": raw, "key_facts": [], "evidence_present": [],
                       "gaps_or_inconsistencies": [], "plain_language": raw}
        summary["source_claim_id"] = claim["claim_id"]
        return summary
