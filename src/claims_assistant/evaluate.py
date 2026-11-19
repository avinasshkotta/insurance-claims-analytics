"""Evaluation harness (Build Lifecycle Phase 6).

A fixed golden set of real policy questions with expected outcomes. Every prompt
or retrieval change must run through this before shipping — the regression gate.

Offline mode (default): retrieval + refusal behavior, deterministic, no API key.
Live mode (--live): the actual model answers, checked for keywords and citations.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .embeddings import EmbeddingIndex
from .prompts import registry
from .rag import PolicyRAG

GOLDEN_PATH = config.PROJECT_ROOT / "eval" / "golden_set.json"


def load_golden(path: Path | None = None) -> dict:
    return json.loads((path or GOLDEN_PATH).read_text(encoding="utf-8"))


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def run_case(case: dict, rag: PolicyRAG) -> dict:
    hits = rag.retrieve(case["query"], case["claim_type"], case["region"])
    docs = [h["doc_id"] for h in hits]
    answer = rag.answer(case["query"], case["claim_type"], case["region"])
    text = answer["answer"]
    cites = answer["citations"]
    refused = text.startswith("REFUSED") or text.startswith("INSUFFICIENT RETRIEVED CONTEXT")

    expected = case.get("expected_doc")
    top1_ok = bool(docs) and docs[0] == expected
    hit_ok = expected in docs if expected else True
    low = text.lower()
    missing = [k for k in case.get("must_include", []) if k.lower() not in low]
    missing_any = [group[0] for group in case.get("must_include_any", [])
                   if not any(term.lower() in low for term in group)]
    leaked = [k for k in case.get("must_not_include", []) if k.lower() in low]
    keywords_ok = not missing and not missing_any and not leaked

    if case.get("should_refuse"):
        refusal_ok = refused and not cites
    else:
        refusal_ok = (not refused) and bool(cites)

    passed = refusal_ok and hit_ok and (top1_ok if expected else True) and keywords_ok
    return {
        "id": case["id"],
        "query": case["query"],
        "expected_doc": expected,
        "top_doc": docs[0] if docs else None,
        "retrieved_docs": docs,
        "refused": refused,
        "citations": cites,
        "missing_keywords": missing,
        "missing_any_keywords": missing_any,
        "leaked_keywords": leaked,
        "checks": {"top1_ok": top1_ok, "retrieval_ok": hit_ok,
                   "refusal_ok": refusal_ok, "keywords_ok": keywords_ok},
        "passed": passed,
    }


def run_eval(llm=None, golden_path: Path | None = None,
             write_report: bool = False) -> dict:
    golden = load_golden(golden_path)
    embeddings = None
    if llm is not None and config.EMBEDDINGS_PATH.exists():
        embeddings = EmbeddingIndex()
    rag = PolicyRAG(llm=llm, embeddings=embeddings)
    results = [run_case(c, rag) for c in golden["cases"]]

    non_refusal = [r for r in results if r["expected_doc"]]
    refusal_cases = [r for r in results if r["expected_doc"] is None]
    total_terms = sum(len(c.get("must_include", []))
                      for c in golden["cases"] if not c.get("should_refuse"))
    hit_terms = total_terms - sum(len(r["missing_keywords"]) for r in non_refusal)

    metrics = {
        "cases": len(results),
        "pass_rate": round(sum(r["passed"] for r in results) / len(results), 4),
        "top1_accuracy": round(sum(r["checks"]["top1_ok"] for r in non_refusal)
                               / max(len(non_refusal), 1), 4),
        "retrieval_hit_rate": round(sum(r["checks"]["retrieval_ok"] for r in non_refusal)
                                    / max(len(non_refusal), 1), 4),
        "refusal_accuracy": round(sum(r["checks"]["refusal_ok"] for r in results)
                                  / len(results), 4),
        "keyword_coverage": round(hit_terms / max(total_terms, 1), 4),
        "refusal_cases": len(refusal_cases),
    }
    thresholds = golden["thresholds"]
    passed = all(metrics[k] >= v for k, v in thresholds.items())

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "live" if llm is not None else "offline",
        "model": getattr(llm, "model", None),
        "prompts": registry(),
        "thresholds": thresholds,
        "metrics": metrics,
        "passed": passed,
        "results": results,
    }
    if write_report:
        config.EVAL_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.EVAL_REPORT_PATH.write_text(json.dumps(report, indent=2),
                                           encoding="utf-8")
    return report


def format_report(report: dict) -> str:
    m = report["metrics"]
    lines = [
        f"Golden-set evaluation · {report['mode']}"
        + (f" · {report['model']}" if report.get("model") else ""),
        "",
        f"  cases               {m['cases']}",
        f"  pass rate           {_pct(m['pass_rate'])}"
        f"   (min {_pct(report['thresholds']['pass_rate'])})",
        f"  top-1 retrieval     {_pct(m['top1_accuracy'])}"
        f"   (min {_pct(report['thresholds']['top1_accuracy'])})",
        f"  retrieval hit rate  {_pct(m['retrieval_hit_rate'])}"
        f"   (min {_pct(report['thresholds']['retrieval_hit_rate'])})",
        f"  refusal accuracy    {_pct(m['refusal_accuracy'])}"
        f"   (min {_pct(report['thresholds']['refusal_accuracy'])})",
        f"  keyword coverage    {_pct(m['keyword_coverage'])}"
        f"   (min {_pct(report['thresholds']['keyword_coverage'])})",
        "",
    ]
    for r in report["results"]:
        mark = "PASS" if r["passed"] else "FAIL"
        extra = []
        if r["missing_keywords"]:
            extra.append(f"missing={r['missing_keywords']}")
        if r.get("missing_any_keywords"):
            extra.append(f"missing_any={r['missing_any_keywords']}")
        if r["leaked_keywords"]:
            extra.append(f"leaked={r['leaked_keywords']}")
        if r["refused"]:
            extra.append("refused")
        lines.append(f"  [{mark}] {r['id']:<26} top={r['top_doc']}"
                     + (f"  {' '.join(extra)}" if extra else ""))
    lines.append("")
    lines.append("RESULT: " + ("PASSED (regression gate clear)" if report["passed"]
                               else "FAILED (below threshold)"))
    return "\n".join(lines)
