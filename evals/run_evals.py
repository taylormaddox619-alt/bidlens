"""Evaluate extraction accuracy and exception detection against labeled ground truth.

Usage:
  python evals/run_evals.py                 # production routing (cheap first pass, escalate on failed checks)
  python evals/run_evals.py --compare       # routed vs. each model alone: accuracy, cost, latency side by side
  python evals/run_evals.py --model claude-haiku-4-5    # a single model, no escalation
  python evals/run_evals.py --demo          # replay recorded extractions (checks the pipeline + rules, no cost)

Writes evals/results.json (read by the AI Scorecard page) and evals/report.md
(--compare also writes evals/model_comparison.md).
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bidlens import config, rules  # noqa: E402
from bidlens.extract import demo_extraction, live_extraction, routed_extraction  # noqa: E402
from bidlens.grading import compare_fields  # noqa: E402
from bidlens.ingest import extract_text, quote_in_document  # noqa: E402
from bidlens.schemas import FIELD_SPECS, RFQ, flat_values  # noqa: E402
from bidlens.credentials import get_secret  # noqa: E402

def grade(truth: dict, quote: dict, text: str) -> dict:
    cites = [(quote.get(name) or {}).get("source_quote") for name, *_ in FIELD_SPECS
             if (quote.get(name) or {}).get("value") is not None]
    return {"fields": compare_fields(truth, quote), "cites_total": len(cites),
            "cites_ok": sum(bool(src and quote_in_document(src, text)) for src in cites)}


def evaluate(name: str, extractor, truths: list[dict], rfq: RFQ) -> dict:
    """Run one configuration over every labeled document and score it."""
    print(f"\n== {name}")
    extractions = {}
    for t in truths:
        text = extract_text(t["filename"], (config.SAMPLES_DIR / t["filename"]).read_bytes())
        quote, meta = extractor(text)
        route = f" (escalated: {'; '.join(meta['escalation_reasons'])})" if meta.get("escalated") else ""
        print(f"  {t['filename']}: {meta.get('status')} {meta.get('model') or ''}{route} {meta.get('error') or ''}")
        extractions[t["filename"]] = (text, quote, meta)

    peers = [rules.unit_price_usd(flat_values(q)) for _, q, _ in extractions.values() if q]
    tp = fp = fn = 0
    docs = []
    for t in truths:
        text, quote, meta = extractions[t["filename"]]
        base = {"filename": t["filename"], "model": meta.get("model"), "escalated": meta.get("escalated", False),
                "cost_usd": meta.get("cost_usd", 0.0), "latency_s": meta.get("latency_s", 0.0)}
        if not quote:
            docs.append({**base, "status": "failed", "error": meta.get("error")})
            fn += len(t["expected_flags"])
            continue
        found = {f.code for f in rules.evaluate(quote, rfq, text, peers)}
        expected = set(t["expected_flags"])
        tp, fp, fn = tp + len(found & expected), fp + len(found - expected), fn + len(expected - found)
        docs.append({**base, "status": "ok", **grade(t["quote"], quote, text),
                     "flags_found": sorted(found), "flags_expected": sorted(expected)})

    graded = [f for d in docs if d["status"] == "ok" for f in d["fields"]]
    failed_fields = sum(len(FIELD_SPECS) + 1 for d in docs if d["status"] != "ok")
    cites_total = sum(d.get("cites_total", 0) for d in docs)
    first_meta = next(iter(extractions.values()))[2]
    source = first_meta.get("recorded_source") or first_meta.get("source")
    return {
        "config": name,
        "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source": "live Claude call" if source == "live" else f"recorded ({source})",
        "model": ", ".join(sorted({d["model"] for d in docs if d.get("model")})) or "n/a",
        "prompt_version": first_meta.get("prompt_version") or config.EXTRACT_PROMPT_VERSION,
        "documents": len(truths),
        "field_accuracy": sum(f["correct"] for f in graded) / (len(graded) + failed_fields) if graded or failed_fields else 0.0,
        "citation_rate": sum(d.get("cites_ok", 0) for d in docs) / cites_total if cites_total else 0.0,
        "flag_recall": tp / (tp + fn) if tp + fn else 1.0,
        "flag_precision": tp / (tp + fp) if tp + fp else 1.0,
        "escalation_rate": sum(d["escalated"] for d in docs) / len(docs),
        "total_cost_usd": sum(d["cost_usd"] for d in docs),
        "cost_per_document_usd": sum(d["cost_usd"] for d in docs) / len(docs),
        "avg_latency_s": sum(d["latency_s"] for d in docs) / len(docs),
        "documents_detail": docs,
    }


def write_report(summary: dict) -> None:
    lines = [
        "# Extraction evaluation report", "",
        f"- Run: {summary['run_at']} · configuration: **{summary['config']}**",
        f"- Source: {summary['source']} · model(s) `{summary['model']}` · prompt `{summary['prompt_version']}`",
        f"- Documents: {summary['documents']}", "",
        "| Metric | Result | Target |", "|---|---|---|",
        f"| Field accuracy | {summary['field_accuracy']:.1%} | ≥ 95% |",
        f"| Citations found in document | {summary['citation_rate']:.1%} | 100% |",
        f"| Exception recall | {summary['flag_recall']:.0%} | 100% |",
        f"| Exception precision | {summary['flag_precision']:.0%} | ≥ 90% |",
        f"| Escalated to stronger model | {summary['escalation_rate']:.0%} | |",
        f"| Cost per document | ${summary['cost_per_document_usd']:.4f} | |",
        f"| Avg latency / document | {summary['avg_latency_s']:.1f} s | |", "",
    ]
    if "fixture" in summary["source"]:
        lines += ["> Note: this run replayed hand-labeled fixtures, so field accuracy is 100% by construction. "
                  "It validates the pipeline and business rules only. Run without `--demo` to measure Claude.", ""]
    lines.append("## Misses")
    misses = 0
    for d in summary["documents_detail"]:
        if d["status"] != "ok":
            lines.append(f"- **{d['filename']}**: extraction failed ({d['error']})")
            misses += 1
            continue
        for f in d["fields"]:
            if not f["correct"]:
                lines.append(f"- **{d['filename']}** `{f['field']}`: expected `{f['expected']}`, got `{f['actual']}`")
                misses += 1
        if d["flags_found"] != d["flags_expected"]:
            lines.append(f"- **{d['filename']}** flags: expected {d['flags_expected']}, found {d['flags_found']}")
            misses += 1
    if not misses:
        lines.append("None.")
    (config.EVALS_DIR / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_comparison(summaries: list[dict]) -> None:
    lines = [
        "# Model routing comparison", "",
        f"Run {summaries[0]['run_at']} · {summaries[0]['documents']} labeled documents · "
        f"prompt `{summaries[0]['prompt_version']}`", "",
        "| Configuration | Field accuracy | Citations | Exception recall | Escalated | Cost / doc | Latency / doc |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(f"| {s['config']} | {s['field_accuracy']:.1%} | {s['citation_rate']:.1%} | {s['flag_recall']:.0%} | "
                     f"{s['escalation_rate']:.0%} | ${s['cost_per_document_usd']:.4f} | {s['avg_latency_s']:.1f} s |")
    lines += ["", "**How to read this:** keep the cheapest configuration whose accuracy and exception recall match the "
                  "strongest model. With only a handful of documents, a one-field difference is within noise; "
                  "add labeled quotes before making a production cutover decision."]
    (config.EVALS_DIR / "model_comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--demo", action="store_true", help="replay recorded extractions instead of calling Claude")
    mode.add_argument("--compare", action="store_true", help="compare routed extraction with each model alone")
    mode.add_argument("--model", help="evaluate a single model with no escalation")
    parser.add_argument("--prompt", default=config.EXTRACT_PROMPT_VERSION)
    args = parser.parse_args()

    api_key = get_secret("ANTHROPIC_API_KEY")
    if not args.demo and not api_key:
        sys.exit("No API key found (environment or .streamlit/secrets.toml). Use --demo to evaluate recorded extractions.")

    rfq = RFQ.model_validate(json.loads((config.SAMPLES_DIR / "demo_rfq.json").read_text(encoding="utf-8")))
    truths = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(config.GROUND_TRUTH_DIR.glob("*.json"))]
    routes = config.MODEL_ROUTES

    def single(model):
        return lambda text: live_extraction(text, rfq, api_key, model, args.prompt)

    def routed(text):
        return routed_extraction(text, rfq, api_key, args.prompt)

    routed_name = f"routed: {routes['extract']} → {routes['extract_escalation']}"
    if args.demo:
        summaries = [evaluate("recorded extractions", demo_extraction, truths, rfq)]
    elif args.compare:
        summaries = [evaluate(f"{routes['extract_escalation']} only", single(routes["extract_escalation"]), truths, rfq),
                     evaluate(f"{routes['extract']} only", single(routes["extract"]), truths, rfq),
                     evaluate(routed_name, routed, truths, rfq)]
        write_comparison(summaries)
    elif args.model:
        summaries = [evaluate(f"{args.model} only", single(args.model), truths, rfq)]
    else:
        summaries = [evaluate(routed_name, routed, truths, rfq)]

    primary = summaries[-1]  # the production (routed) configuration when comparing
    (config.EVALS_DIR / "results.json").write_text(json.dumps(primary, indent=2, default=str), encoding="utf-8")
    write_report(primary)

    print()
    for s in summaries:
        print(f"{s['config']:45s} accuracy {s['field_accuracy']:.1%} · citations {s['citation_rate']:.1%} · "
              f"recall {s['flag_recall']:.0%} · escalated {s['escalation_rate']:.0%} · "
              f"${s['cost_per_document_usd']:.4f}/doc")
    print("wrote evals/results.json and evals/report.md" + (" and evals/model_comparison.md" if args.compare else ""))


if __name__ == "__main__":
    main()
