"""Evaluate extraction accuracy and exception detection against labeled ground truth.

Usage:
  python evals/run_evals.py            # live: calls Claude for every sample (needs ANTHROPIC_API_KEY)
  python evals/run_evals.py --demo     # replay recorded extractions (checks the pipeline + rules, no cost)
  python evals/run_evals.py --prompt extract_v2 --model claude-sonnet-5   # compare a prompt/model variant

Writes evals/results.json (read by the AI Scorecard page) and evals/report.md.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bidlens import config, rules  # noqa: E402
from bidlens.extract import demo_extraction, live_extraction  # noqa: E402
from bidlens.ingest import extract_text, quote_in_document  # noqa: E402
from bidlens.schemas import FIELD_SPECS, RFQ, flat_values  # noqa: E402


def values_match(kind: str, expected, actual) -> bool:
    if expected is None or actual is None:
        return expected is None and actual is None
    if kind == "number":
        return abs(float(expected) - float(actual)) <= max(0.005 * abs(float(expected)), 0.01)
    norm = lambda s: " ".join(str(s).lower().replace(".", "").replace(",", "").split())  # noqa: E731
    return norm(expected) == norm(actual)


# Free-text fields are graded leniently: the expected text must appear in the extraction or vice versa.
LENIENT = {"supplier_name", "payment_terms", "incoterm_location"}


def grade(truth: dict, quote: dict, text: str) -> dict:
    results, cites_ok, cites_total = [], 0, 0
    for name, _, kind, _ in FIELD_SPECS:
        exp = truth[name]["value"]
        got = (quote.get(name) or {}).get("value")
        if name in LENIENT and exp is not None and got is not None:
            ok = str(exp).lower() in str(got).lower() or str(got).lower() in str(exp).lower()
        else:
            ok = values_match(kind, exp, got)
        results.append({"field": name, "expected": exp, "actual": got, "correct": ok})
        src = (quote.get(name) or {}).get("source_quote")
        if got is not None:
            cites_total += 1
            cites_ok += bool(src and quote_in_document(src, text))
    exp_tiers = sorted((t["min_qty"], t["unit_price"]) for t in truth["price_tiers"])
    got_tiers = sorted((t["min_qty"], t["unit_price"]) for t in quote.get("price_tiers") or [])
    results.append({"field": "price_tiers", "expected": exp_tiers, "actual": got_tiers, "correct": exp_tiers == got_tiers})
    return {"fields": results, "cites_ok": cites_ok, "cites_total": cites_total}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="use recorded extractions instead of calling Claude")
    parser.add_argument("--model", default=config.DEFAULT_MODEL)
    parser.add_argument("--prompt", default=config.EXTRACT_PROMPT_VERSION)
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not args.demo and not api_key:
        sys.exit("ANTHROPIC_API_KEY is not set. Use --demo to evaluate recorded extractions.")

    rfq = RFQ.model_validate(json.loads((config.SAMPLES_DIR / "demo_rfq.json").read_text(encoding="utf-8")))
    truths = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(config.GROUND_TRUTH_DIR.glob("*.json"))]

    docs, extractions = [], {}
    for t in truths:
        text = extract_text(t["filename"], (config.SAMPLES_DIR / t["filename"]).read_bytes())
        quote, meta = demo_extraction(text) if args.demo else live_extraction(text, rfq, api_key, args.model, args.prompt)
        print(f"{t['filename']}: {meta.get('status')} {meta.get('error') or ''}")
        extractions[t["filename"]] = (text, quote, meta)

    peers = [rules.unit_price_usd(flat_values(q)) for _, q, _ in extractions.values() if q]
    tp = fp = fn = 0
    total_cost = total_latency = 0.0
    source = None
    for t in truths:
        text, quote, meta = extractions[t["filename"]]
        total_cost += meta.get("cost_usd", 0.0)
        total_latency += meta.get("latency_s", 0.0)
        source = meta.get("recorded_source") or meta.get("source")
        if not quote:
            docs.append({"filename": t["filename"], "status": "failed", "error": meta.get("error")})
            fn += len(t["expected_flags"])
            continue
        g = grade(t["quote"], quote, text)
        found = {f.code for f in rules.evaluate(quote, rfq, text, peers)}
        expected = set(t["expected_flags"])
        tp += len(found & expected)
        fp += len(found - expected)
        fn += len(expected - found)
        docs.append({"filename": t["filename"], "status": "ok", **g,
                     "flags_found": sorted(found), "flags_expected": sorted(expected),
                     "cost_usd": meta.get("cost_usd", 0.0), "latency_s": meta.get("latency_s", 0.0)})

    graded = [f for d in docs if d["status"] == "ok" for f in d["fields"]]
    total_fields = len(FIELD_SPECS) + 1
    failed_fields = sum(total_fields for d in docs if d["status"] != "ok")
    correct = sum(f["correct"] for f in graded)
    cites_ok = sum(d.get("cites_ok", 0) for d in docs)
    cites_total = sum(d.get("cites_total", 0) for d in docs)

    summary = {
        "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source": "live Claude call" if source == "live" else f"recorded ({source})",
        "model": args.model if not args.demo else next(
            (m.get("model") for _, _, m in extractions.values() if m.get("model")), "n/a"),
        "prompt_version": args.prompt if not args.demo else next(
            (m.get("prompt_version") for _, _, m in extractions.values() if m.get("prompt_version")), "n/a"),
        "documents": len(truths),
        "field_accuracy": correct / (len(graded) + failed_fields) if graded or failed_fields else 0.0,
        "citation_rate": cites_ok / cites_total if cites_total else 0.0,
        "flag_recall": tp / (tp + fn) if tp + fn else 1.0,
        "flag_precision": tp / (tp + fp) if tp + fp else 1.0,
        "total_cost_usd": total_cost,
        "avg_latency_s": total_latency / len(truths),
        "documents_detail": docs,
    }
    (config.EVALS_DIR / "results.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    lines = [
        "# Extraction evaluation report", "",
        f"- Run: {summary['run_at']}",
        f"- Source: {summary['source']} · model `{summary['model']}` · prompt `{summary['prompt_version']}`",
        f"- Documents: {summary['documents']}", "",
        "| Metric | Result | Target |", "|---|---|---|",
        f"| Field accuracy | {summary['field_accuracy']:.1%} | ≥ 95% |",
        f"| Citations found in document | {summary['citation_rate']:.1%} | 100% |",
        f"| Exception recall | {summary['flag_recall']:.0%} | 100% |",
        f"| Exception precision | {summary['flag_precision']:.0%} | ≥ 90% |",
        f"| Total API cost | ${summary['total_cost_usd']:.4f} | |",
        f"| Avg latency / document | {summary['avg_latency_s']:.1f} s | |", "",
    ]
    if summary["source"].startswith("recorded (fixture)"):
        lines += ["> Note: this run replayed hand-labeled fixtures, so field accuracy is 100% by construction. "
                  "It validates the pipeline and business rules only. Run without `--demo` to measure Claude.", ""]
    lines.append("## Misses")
    misses = 0
    for d in docs:
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

    print(f"\nfield accuracy {summary['field_accuracy']:.1%} · citations {summary['citation_rate']:.1%} · "
          f"flag recall {summary['flag_recall']:.0%} · precision {summary['flag_precision']:.0%} · "
          f"cost ${total_cost:.4f}")
    print("wrote evals/results.json and evals/report.md")


if __name__ == "__main__":
    main()
