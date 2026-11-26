"""Command-line entry point.

  python -m claims_assistant.cli triage [--via-mcp] [--claims CLM-1041 ...]
  python -m claims_assistant.cli ask CLM-1042 "Am I covered for rental reimbursement?"
  python -m claims_assistant.cli eval [--live]
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from . import config, usage
from .agent import run_agent
from .embeddings import EmbeddingIndex
from .evaluate import format_report, run_eval
from .llm import LLMClient
from .rag import PolicyRAG
from .services import CaseQueue, ClaimsDB
from .toolkit import build_direct_handlers
from .tracing import record_run


def _build(llm):
    db = ClaimsDB()
    handlers = build_direct_handlers(llm, db, PolicyRAG(llm=llm), CaseQueue())
    return db, handlers


def _require_key() -> bool:
    if not config.OPENAI_API_KEY:
        print("OPENAI_API_KEY not set — copy .env.example to .env and add your key.",
              file=sys.stderr)
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="claims_assistant")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_triage = sub.add_parser("triage", help="run the agent over claims")
    p_triage.add_argument("--claims", nargs="*",
                          help="claim ids (default: all open/under_review)")
    p_triage.add_argument("--via-mcp", action="store_true",
                          help="route all tool calls through the MCP server")
    p_triage.add_argument("--threshold", type=float,
                          help="override review threshold (review-capacity knob)")

    p_ask = sub.add_parser("ask", help="ask a policy question about a claim")
    p_ask.add_argument("claim_id")
    p_ask.add_argument("question")
    p_ask.add_argument("--via-mcp", action="store_true")

    p_eval = sub.add_parser("eval", help="run the golden-set evaluation harness")
    p_eval.add_argument("--live", action="store_true",
                        help="evaluate the real LLM answers (needs API key)")
    p_eval.add_argument("--dataset", default=None,
                        help="path to a case file (default: eval/golden_set.json)")
    p_eval.add_argument("--no-report", action="store_true",
                        help="do not write docs/eval_report.json")

    sub.add_parser("reindex", help="build the semantic index for policy documents")
    sub.add_parser("ml", help="train the severity model + clusters (synthetic history)")
    sub.add_parser("drift", help="PSI drift report vs the training baseline")
    sub.add_parser("fairness", help="segment parity audit for triage outcomes")

    args = parser.parse_args(argv)

    threshold = getattr(args, "threshold", None)  # only `triage` defines it
    if threshold is not None:
        config.RISK_THRESHOLD = threshold

    if args.cmd == "eval":
        llm = None
        if args.live:
            if not _require_key():
                return 2
            llm = LLMClient()
        report = run_eval(llm=llm, write_report=not args.no_report,
                          golden_path=Path(args.dataset) if args.dataset else None)
        print(format_report(report))
        return 0 if report["passed"] else 1

    if args.cmd == "reindex":
        if not _require_key():
            return 2
        llm = LLMClient()
        rag = PolicyRAG(llm=llm)
        index = EmbeddingIndex()
        added = index.build(rag.all_chunks(), llm.embed)
        print(f"Embedded {added} new chunk(s) · {len(index)} total · {index.model}")
        print(f"Index: {index.path}")
        return 0

    if args.cmd == "ml":
        from .severity import train
        rep = train()
        m = rep["metrics"]
        print(f"Severity model · {m['rows']} rows ({m['test_rows']} held out)")
        print(f"  MAE        ${m['mae']:,.0f}   (baseline ${m['baseline_mae']:,.0f})")
        print(f"  RMSE       ${m['rmse']:,.0f}")
        print(f"  R2         {m['r2']:.3f}")
        print("\nClusters (by mean severity):")
        for c in rep["clusters"]:
            print(f"  C{c['cluster']} {c['label']:<13} n={c['n']:<4} "
                  f"mean=${c['mean_severity']:>10,.0f}  days={c['mean_days_to_report']:<4} "
                  f"attorney={c['attorney_rate']:.0%}  evidence={c['mean_evidence']}")
        print("\nTop SHAP features:", ", ".join(rep["top_features"][:5]))
        return 0

    if args.cmd == "drift":
        from .drift import drift_report
        try:
            rep = drift_report()
        except FileNotFoundError as exc:
            print(f"{exc}. Run: python -m claims_assistant.cli ml")
            return 1
        print(f"Drift report · {rep['recent_rows']} recent rows · verdict: {rep['verdict'].upper()}")
        for name, v in rep["features"].items():
            bar = "#" * min(40, int(v["psi"] * 100))
            print(f"  {name:<18} PSI {v['psi']:>6.3f}  {v['status']:<8} {bar}")
        return 0

    if args.cmd == "fairness":
        from .fairness import segment_report
        try:
            rep = segment_report()
        except FileNotFoundError as exc:
            print(f"{exc}. Run: python -m claims_assistant.cli ml")
            return 1
        print(f"Segment parity · {rep['rows']} rows · cutoff ${rep['cutoff_severity']:,.0f}"
              f" · overall rate {rep['selection_rate_overall']:.1%}")
        for key in ("by_region", "by_claim_type", "by_channel"):
            print(f"\n{key}:")
            for seg, rate in rep[key].items():
                print(f"  {seg:<10} {rate:.1%}")
        print(f"\nParity gaps: region {rep['gap_region']:.3f} · "
              f"type {rep['gap_claim_type']:.3f} · channel {rep['gap_channel']:.3f}")
        print("Protected attributes excluded from features:",
              ", ".join(rep["protected_attributes_excluded_from_features"]) or "none recorded")
        return 0

    if not _require_key():
        return 2

    llm = LLMClient()
    db, handlers = _build(llm)

    if args.cmd == "triage":
        claim_ids = args.claims or [c["claim_id"] for c in db.list()
                                    if c["status"] in ("open", "under_review")]
        result = asyncio.run(
            run_agent(claim_ids, None, llm,
                      via_mcp=args.via_mcp, handlers=handlers))
    else:  # ask
        result = asyncio.run(
            run_agent([args.claim_id], args.question, llm,
                      via_mcp=args.via_mcp, handlers=handlers))

    print(result["final"])
    run = record_run(args.cmd, claim_ids if args.cmd == "triage" else [args.claim_id],
                     result["tool_log"], result["final"], model=llm.model)
    tools_used = ", ".join(t["tool"] for t in result["tool_log"]) or "none"
    print(f"\n[{len(result['tool_log'])} tool calls"
          f"{' · via MCP' if args.via_mcp else ''}: {tools_used}]  ·  {run['run_id']}",
          file=sys.stderr)
    spent = usage.summary()
    if spent["calls"]:
        print(f"[usage: {spent['calls']} calls · "
              f"{spent['input_tokens'] + spent['output_tokens']:,} tokens · "
              f"${spent['cost_usd']:.4f} lifetime]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
