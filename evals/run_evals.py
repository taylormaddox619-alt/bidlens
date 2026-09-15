"""Evaluate extraction accuracy, exception detection, cost and latency against labeled ground truth.

Usage:
  python evals/run_evals.py                          # production routing, one trial
  python evals/run_evals.py --trials 3               # repeat every document 3x: mean, min-max, p50/p95
  python evals/run_evals.py --compare --trials 3     # routing decision table: each model alone, effort levels, routed
  python evals/run_evals.py --configs sonnet-low,routed
  python evals/run_evals.py --model claude-haiku-4-5 # a single model, no escalation
  python evals/run_evals.py --demo                   # replay recorded extractions (pipeline + rules check, no cost)

Writes evals/results.json (read by the AI Scorecard page) and evals/report.md, appends one line per
configuration to evals/history.jsonl, and with --compare also writes evals/model_comparison.md.
--demo writes results_demo.json / report_demo.md instead and never touches the history.
"""

import argparse
import json
import statistics
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Windows consoles default to cp1252; never let a progress line kill a paid run.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")

from bidlens import config, rules  # noqa: E402
from bidlens.credentials import get_secret  # noqa: E402
from bidlens.extract import demo_extraction, escalation_reasons, live_extraction, routed_extraction  # noqa: E402
from bidlens.grading import compare_fields  # noqa: E402
from bidlens.ingest import extract_text, quote_in_document  # noqa: E402
from bidlens.schemas import FIELD_SPECS, RFQ, flat_values  # noqa: E402

TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens")


# --- Configurations -----------------------------------------------------------------
def build_configs(rfq: RFQ, api_key: str | None, prompt: str) -> dict[str, dict]:
    """Named extractor configurations. `extractor(text) -> (quote, meta)`."""
    r = config.MODEL_ROUTES
    strong, cheap = r["extract_escalation"], r["extract"]

    def single(model, effort=None):
        return lambda text: live_extraction(text, rfq, api_key, model, prompt, effort=effort)

    def routed(effort=None):
        efforts = {"extract": effort, "extract_escalation": config.EFFORT["extract_escalation"]}
        return lambda text: routed_extraction(text, rfq, api_key, prompt, efforts=efforts)

    return {
        "opus-only": {"label": f"{strong} only", "extractor": single(strong), "effort": None, "routed": False},
        "sonnet-only": {"label": f"{cheap} only", "extractor": single(cheap), "effort": None, "routed": False},
        "sonnet-low": {"label": f"{cheap} only, effort=low", "extractor": single(cheap, "low"), "effort": "low",
                       "routed": False},
        "sonnet-medium": {"label": f"{cheap} only, effort=medium", "extractor": single(cheap, "medium"),
                          "effort": "medium", "routed": False},
        "routed": {"label": f"routed: {cheap} → {strong}", "extractor": routed(config.EFFORT["extract"]),
                   "effort": config.EFFORT["extract"], "routed": True},
        "routed-low": {"label": f"routed: {cheap} (effort=low) → {strong}", "extractor": routed("low"),
                       "effort": "low", "routed": True},
        "demo": {"label": "recorded extractions", "extractor": demo_extraction, "effort": None, "routed": True},
    }


COMPARE_SET = ["opus-only", "sonnet-only", "sonnet-low", "sonnet-medium", "routed", "routed-low"]


# --- One trial ----------------------------------------------------------------------
def grade(truth: dict, quote: dict, text: str) -> dict:
    fields = compare_fields(truth, quote)
    for f in fields:
        field = quote.get(f["field"])
        if not isinstance(field, dict) or f["actual"] is None:  # price_tiers is a list; nulls have no citation
            f["confidence"], f["cited"] = None, None
            continue
        f["confidence"] = field.get("confidence")
        src = field.get("source_quote")
        f["cited"] = bool(src and quote_in_document(src, text))
    cited = [f["cited"] for f in fields if f["cited"] is not None]
    return {"fields": fields, "cites_total": len(cited), "cites_ok": sum(cited)}


def evaluate_once(name: str, cfg: dict, truths: list[dict], rfq: RFQ, workers: int = 4) -> dict:
    """Run one configuration over every labeled document once and score it."""
    def run(t):
        text = extract_text(t["filename"], (config.SAMPLES_DIR / t["filename"]).read_bytes())
        quote, meta = cfg["extractor"](text)
        return text, quote, meta

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        extractions = dict(zip((t["filename"] for t in truths), pool.map(run, truths)))
    for t in truths:
        _, _, meta = extractions[t["filename"]]
        route = f" (escalated: {'; '.join(meta['escalation_reasons'])})" if meta.get("escalated") else ""
        print(f"  {t['filename']}: {meta.get('status')} {meta.get('model') or ''}{route} "
              f"{meta.get('latency_s', 0):.1f}s ${meta.get('cost_usd', 0):.4f} {meta.get('error') or ''}")

    peers = [rules.unit_price_usd(flat_values(q)) for _, q, _ in extractions.values() if q]
    tp = fp = fn = 0
    docs = []
    for t in truths:
        text, quote, meta = extractions[t["filename"]]
        base = {"filename": t["filename"], "model": meta.get("model"), "escalated": bool(meta.get("escalated")),
                "cost_usd": meta.get("cost_usd", 0.0), "latency_s": meta.get("latency_s", 0.0),
                "effort": meta.get("effort"), **{k: meta.get(k, 0) or 0 for k in TOKEN_KEYS}}
        if not quote:
            docs.append({**base, "status": "failed", "error": meta.get("error"), "gate_fired": True})
            fn += len(t["expected_flags"])
            continue
        found = {f.code for f in rules.evaluate(quote, rfq, text, peers)}
        expected = set(t["expected_flags"])
        tp, fp, fn = tp + len(found & expected), fp + len(found - expected), fn + len(expected - found)
        # For a single model, "gate fired" means the quality gate would have escalated this output.
        # For routed configs, it means the first pass actually escalated (the quote is then the second pass).
        gate = meta.get("escalation_reasons") if cfg["routed"] else escalation_reasons(quote, meta, text)
        docs.append({**base, "status": "ok", **grade(t["quote"], quote, text),
                     "flags_found": sorted(found), "flags_expected": sorted(expected),
                     "gate_fired": bool(gate), "gate_reasons": gate or []})

    graded = [f for d in docs if d["status"] == "ok" for f in d["fields"]]
    failed_fields = sum(len(FIELD_SPECS) + 1 for d in docs if d["status"] != "ok")
    cites_total = sum(d.get("cites_total", 0) for d in docs)
    first_meta = next(iter(extractions.values()))[2]
    source = first_meta.get("recorded_source") or first_meta.get("source")
    return {
        "config": name, "label": cfg["label"],
        "source": "live Claude call" if source == "live" else f"recorded ({source})",
        "model": ", ".join(sorted({d["model"] for d in docs if d.get("model")})) or "n/a",
        "prompt_version": first_meta.get("prompt_version") or config.EXTRACT_PROMPT_VERSION,
        "field_accuracy": sum(f["correct"] for f in graded) / (len(graded) + failed_fields) if graded or failed_fields else 0.0,
        "citation_rate": sum(d.get("cites_ok", 0) for d in docs) / cites_total if cites_total else 0.0,
        "flag_recall": tp / (tp + fn) if tp + fn else 1.0,
        "flag_precision": tp / (tp + fp) if tp + fp else 1.0,
        "documents_detail": docs,
    }


# --- Aggregation over trials --------------------------------------------------------
def percentile(values: list[float], p: float) -> float:
    """Linear-interpolated percentile (same convention as numpy's default)."""
    if not values:
        return 0.0
    xs = sorted(values)
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _spread(values: list[float]) -> dict:
    return {"mean": statistics.fmean(values), "min": min(values), "max": max(values)}


def gate_stats(docs: list[dict]) -> dict:
    """2x2 of gate fired x extraction wrong. Precision = fired runs that were wrong; recall = wrong runs caught."""
    fired_wrong = fired_ok = quiet_wrong = quiet_ok = 0
    for d in docs:
        wrong = d["status"] != "ok" or not all(f["correct"] for f in d["fields"])
        fired = d.get("gate_fired", False)
        if fired and wrong:
            fired_wrong += 1
        elif fired:
            fired_ok += 1
        elif wrong:
            quiet_wrong += 1
        else:
            quiet_ok += 1
    fired, wrong, n = fired_wrong + fired_ok, fired_wrong + quiet_wrong, len(docs)
    return {"fired_wrong": fired_wrong, "fired_ok": fired_ok, "quiet_wrong": quiet_wrong, "quiet_ok": quiet_ok,
            "precision": fired_wrong / fired if fired else None,
            "recall": fired_wrong / wrong if wrong else None,
            "false_fire_rate": fired_ok / n if n else 0.0, "n": n}


def calibration(docs: list[dict]) -> dict:
    """Does the model's self-reported confidence track correctness? {level: {count, correct_rate}}."""
    out = {}
    for d in docs:
        for f in d.get("fields", []):
            if f["actual"] is None or not f.get("confidence"):
                continue
            bucket = out.setdefault(f["confidence"], {"count": 0, "correct": 0})
            bucket["count"] += 1
            bucket["correct"] += int(f["correct"])
    return {level: {"count": b["count"], "correct_rate": b["correct"] / b["count"]}
            for level, b in sorted(out.items(), key=lambda kv: ["high", "medium", "low"].index(kv[0])
                                   if kv[0] in ("high", "medium", "low") else 9)}


def per_field_accuracy(docs: list[dict]) -> dict:
    out = {}
    for d in docs:
        for f in d.get("fields", []):
            bucket = out.setdefault(f["field"], {"count": 0, "correct": 0})
            bucket["count"] += 1
            bucket["correct"] += int(f["correct"])
    return {name: b["correct"] / b["count"] for name, b in out.items() if b["count"]}


def aggregate(trials: list[dict]) -> dict:
    """Summarize repeated trials of one configuration. Keeps every key the scorecard already reads."""
    docs = [d for t in trials for d in t["documents_detail"]]
    latencies = [d["latency_s"] for d in docs]
    tokens = {k: sum(d.get(k, 0) for d in docs) for k in TOKEN_KEYS}
    cached_prefix = tokens["cache_read_tokens"] + tokens["cache_write_tokens"]
    all_input = tokens["input_tokens"] + cached_prefix
    last = trials[-1]
    n_docs = len(last["documents_detail"])
    return {
        "config": last["label"], "config_key": last["config"],
        "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source": last["source"], "model": last["model"], "prompt_version": last["prompt_version"],
        "effort": next((d.get("effort") for d in docs if d.get("effort")), None),
        "documents": n_docs, "trials": len(trials), "n": len(docs),
        "field_accuracy": statistics.fmean(t["field_accuracy"] for t in trials),
        "field_accuracy_spread": _spread([t["field_accuracy"] for t in trials]),
        "citation_rate": statistics.fmean(t["citation_rate"] for t in trials),
        "citation_rate_spread": _spread([t["citation_rate"] for t in trials]),
        "flag_recall": statistics.fmean(t["flag_recall"] for t in trials),
        "flag_recall_spread": _spread([t["flag_recall"] for t in trials]),
        "flag_precision": statistics.fmean(t["flag_precision"] for t in trials),
        "escalation_rate": sum(d["escalated"] for d in docs) / len(docs),
        "total_cost_usd": sum(d["cost_usd"] for d in docs),
        "cost_per_document_usd": sum(d["cost_usd"] for d in docs) / len(docs),
        "cost_per_document_spread": _spread([d["cost_usd"] for d in docs]),
        "avg_latency_s": statistics.fmean(latencies),
        "latency_p50_s": percentile(latencies, 0.5), "latency_p95_s": percentile(latencies, 0.95),
        "tokens_per_document": {k: v / len(docs) for k, v in tokens.items()},
        "cache_hit_rate": tokens["cache_read_tokens"] / all_input if all_input else 0.0,
        "cached_prefix_share": cached_prefix / all_input if all_input else 0.0,
        "gate": gate_stats(docs), "calibration": calibration(docs), "per_field": per_field_accuracy(docs),
        "documents_detail": last["documents_detail"],
        "trials_detail": [t["documents_detail"] for t in trials] if len(trials) > 1 else None,
    }


# --- Reports ------------------------------------------------------------------------
def _pct(x, digits=1) -> str:
    return "n/a" if x is None else f"{x:.{digits}%}"


def _acc(summary: dict, key: str) -> str:
    s = summary.get(f"{key}_spread")
    if not s or summary["trials"] == 1 or s["min"] == s["max"]:
        return _pct(summary[key])
    return f"{summary[key]:.1%} ({s['min']:.1%}–{s['max']:.1%})"


def gate_lines(summary: dict) -> list[str]:
    g = summary["gate"]
    return [
        "## Quality gate (escalation trigger)", "",
        "The gate fires when a citation is missing from the document, a required field has low confidence, no price "
        "was found, or a date/country is unreadable after normalization. Below, *wrong* means at least one field "
        "disagreed with ground truth.", "",
        "| | Extraction wrong | Extraction right |", "|---|---|---|",
        f"| Gate fired | {g['fired_wrong']} | {g['fired_ok']} |",
        f"| Gate quiet | {g['quiet_wrong']} | {g['quiet_ok']} |", "",
        f"- Precision (fired runs that were actually wrong): {_pct(g['precision'], 0)}",
        f"- Recall (wrong runs the gate caught): {_pct(g['recall'], 0)}",
        f"- False-fire rate (needless escalations): {_pct(g['false_fire_rate'], 0)} of {g['n']} runs", "",
    ]


def calibration_lines(summary: dict) -> list[str]:
    lines = ["## Confidence calibration", "",
             "Self-reported confidence per extracted field, against ground truth.", "",
             "| Confidence | Fields | Correct |", "|---|---|---|"]
    for level, b in summary["calibration"].items():
        lines.append(f"| {level} | {b['count']} | {b['correct_rate']:.1%} |")
    return lines + [""]


def per_field_lines(summary: dict) -> list[str]:
    weak = sorted(((v, k) for k, v in summary["per_field"].items() if v < 1.0))
    if not weak:
        return ["## Per-field accuracy", "", "Every field was correct in every trial.", ""]
    return ["## Per-field accuracy (fields below 100%)", "", "| Field | Accuracy |", "|---|---|"] + \
        [f"| {k} | {v:.1%} |" for v, k in weak] + [""]


def write_report(summary: dict, path: Path) -> None:
    t = summary["trials"]
    lines = [
        "# Extraction evaluation report", "",
        f"- Run: {summary['run_at']} · configuration: **{summary['config']}**",
        f"- Source: {summary['source']} · model(s) `{summary['model']}` · prompt `{summary['prompt_version']}`"
        + (f" · effort `{summary['effort']}`" if summary.get("effort") else ""),
        f"- Documents: {summary['documents']} · trials: {t} · runs scored: {summary['n']}", "",
        "| Metric | Result | Target |", "|---|---|---|",
        f"| Field accuracy | {_acc(summary, 'field_accuracy')} | ≥ 95% |",
        f"| Citations found in document | {_acc(summary, 'citation_rate')} | 100% |",
        f"| Exception recall | {_acc(summary, 'flag_recall')} | 100% |",
        f"| Exception precision | {summary['flag_precision']:.0%} | ≥ 90% |",
        f"| Escalated to stronger model | {summary['escalation_rate']:.0%} | |",
        f"| Cost per document | ${summary['cost_per_document_usd']:.4f} "
        f"(${summary['cost_per_document_spread']['min']:.4f}–${summary['cost_per_document_spread']['max']:.4f}) | |",
        f"| Latency p50 / p95 (n={summary['n']}) | {summary['latency_p50_s']:.1f} s / {summary['latency_p95_s']:.1f} s | |",
        f"| Prompt cache hit rate | {summary['cache_hit_rate']:.0%} of input tokens | |", "",
    ]
    if "fixture" in summary["source"] or "demo" in summary["source"]:
        lines += ["> Note: this run replayed recorded extractions, so accuracy reflects the recording, not a fresh "
                  "model call. It validates the pipeline and business rules. Run without `--demo` to measure Claude.", ""]
    if summary["n"] < 30:
        lines += [f"> With {summary['n']} scored runs, p95 is close to the maximum and a single field is "
                  f"{1 / (summary['n'] * (len(FIELD_SPECS) + 1)):.1%} of accuracy. Treat small differences as noise.", ""]
    lines += gate_lines(summary) + calibration_lines(summary) + per_field_lines(summary)
    lines.append("## Misses (last trial)")
    misses = 0
    for d in summary["documents_detail"]:
        if d["status"] != "ok":
            lines.append(f"- **{d['filename']}**: extraction failed ({d['error']})")
            misses += 1
            continue
        for f in d["fields"]:
            if not f["correct"]:
                lines.append(f"- **{d['filename']}** `{f['field']}`: expected `{f['expected']}`, got `{f['actual']}` "
                             f"(confidence {f.get('confidence')})")
                misses += 1
        if d["flags_found"] != d["flags_expected"]:
            lines.append(f"- **{d['filename']}** flags: expected {d['flags_expected']}, found {d['flags_found']}")
            misses += 1
    if not misses:
        lines.append("None.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_comparison(summaries: list[dict], path: Path) -> None:
    s0 = summaries[0]
    lines = [
        "# Model routing comparison", "",
        f"Run {s0['run_at']} · {s0['documents']} labeled documents × {s0['trials']} trial(s) = {s0['n']} runs per "
        f"configuration · prompt `{s0['prompt_version']}`", "",
        "| Configuration | Field accuracy | Citations | Exception recall | Escalated | Cost / doc | p50 | p95 | Cache hit |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(f"| {s['config']} | {_acc(s, 'field_accuracy')} | {_acc(s, 'citation_rate')} | "
                     f"{_acc(s, 'flag_recall')} | {s['escalation_rate']:.0%} | ${s['cost_per_document_usd']:.4f} | "
                     f"{s['latency_p50_s']:.1f} s | {s['latency_p95_s']:.1f} s | {s['cache_hit_rate']:.0%} |")
    lines += ["", "Accuracy columns show the mean over trials with the min–max range when trials differ. "
              f"Latency percentiles are over {s0['n']} document runs per configuration.", "",
              "**How to read this:** keep the cheapest configuration whose accuracy and exception recall match the "
              "strongest model across every trial. With a handful of documents, a one-field difference is within "
              "noise; add labeled quotes before making a production cutover decision.", "",
              "## Quality gate per configuration", "",
              "| Configuration | Gate fired | Fired & wrong | Quiet & wrong | Precision | Recall | False fires |",
              "|---|---|---|---|---|---|---|"]
    for s in summaries:
        g = s["gate"]
        lines.append(f"| {s['config']} | {g['fired_wrong'] + g['fired_ok']} / {g['n']} | {g['fired_wrong']} | "
                     f"{g['quiet_wrong']} | {_pct(g['precision'], 0)} | {_pct(g['recall'], 0)} | "
                     f"{_pct(g['false_fire_rate'], 0)} |")
    lines += ["", "For single-model rows, *gate fired* means the production gate would have escalated that output. "
              "For routed rows it means the first pass did escalate, and correctness refers to the final output.", "",
              "## Confidence calibration (all configurations pooled)", "",
              "| Confidence | Fields | Correct |", "|---|---|---|"]
    pooled = calibration([d for s in summaries for t in (s["trials_detail"] or [s["documents_detail"]]) for d in t])
    for level, b in pooled.items():
        lines.append(f"| {level} | {b['count']} | {b['correct_rate']:.1%} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _git_sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              timeout=5).stdout.strip() or None
    except Exception:
        return None


def append_history(summaries: list[dict], path: Path) -> None:
    """One JSON line per configuration per run, so the scorecard can chart accuracy and cost over time."""
    sha = _git_sha()
    skip = {"documents_detail", "trials_detail", "per_field"}
    with path.open("a", encoding="utf-8") as fh:
        for s in summaries:
            fh.write(json.dumps({"git": sha, **{k: v for k, v in s.items() if k not in skip}}, default=str) + "\n")


# --- CLI ----------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> list[dict]:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--demo", action="store_true", help="replay recorded extractions instead of calling Claude")
    mode.add_argument("--compare", action="store_true", help="routing decision table: models, effort levels, routed")
    mode.add_argument("--model", help="evaluate a single model with no escalation")
    mode.add_argument("--configs", help="comma-separated subset: " + ", ".join(COMPARE_SET))
    parser.add_argument("--prompt", default=config.EXTRACT_PROMPT_VERSION)
    parser.add_argument("--trials", type=int, default=1, help="repeat every document this many times")
    parser.add_argument("--workers", type=int, default=4, help="documents extracted concurrently")
    parser.add_argument("--out", help="results file name (default results.json, or results_demo.json with --demo)")
    args = parser.parse_args(argv)

    api_key = get_secret("ANTHROPIC_API_KEY")
    if not args.demo and not api_key:
        sys.exit("No API key found (environment or .streamlit/secrets.toml). Use --demo to evaluate recorded extractions.")

    rfq = RFQ.model_validate(json.loads((config.SAMPLES_DIR / "demo_rfq.json").read_text(encoding="utf-8")))
    truths = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(config.GROUND_TRUTH_DIR.glob("*.json"))]
    configs = build_configs(rfq, api_key, args.prompt)

    if args.demo:
        names = ["demo"]
    elif args.compare:
        names = COMPARE_SET
    elif args.configs:
        names = [n.strip() for n in args.configs.split(",")]
        unknown = [n for n in names if n not in configs]
        if unknown:
            sys.exit(f"Unknown configuration(s): {', '.join(unknown)}. Choose from: {', '.join(COMPARE_SET)}")
    elif args.model:
        configs["custom"] = {"label": f"{args.model} only", "effort": None, "routed": False,
                             "extractor": lambda text: live_extraction(text, rfq, api_key, args.model, args.prompt)}
        names = ["custom"]
    else:
        names = ["routed"]

    summaries = []
    partial = config.EVALS_DIR / "results_partial.json"  # survives a crash mid-comparison
    for name in names:
        trials = []
        for i in range(args.trials):
            print(f"\n== {configs[name]['label']}" + (f" · trial {i + 1}/{args.trials}" if args.trials > 1 else ""))
            trials.append(evaluate_once(name, configs[name], truths, rfq, args.workers))
        summaries.append(aggregate(trials))
        partial.write_text(json.dumps(summaries, indent=2, default=str), encoding="utf-8")

    out_name = args.out or ("results_demo.json" if args.demo else "results.json")
    report_name = "report_demo.md" if args.demo else "report.md"
    primary = summaries[-1]  # the production (routed) configuration when comparing
    (config.EVALS_DIR / out_name).write_text(json.dumps(primary, indent=2, default=str), encoding="utf-8")
    write_report(primary, config.EVALS_DIR / report_name)
    written = [out_name, report_name]
    if len(summaries) > 1:
        write_comparison(summaries, config.EVALS_DIR / "model_comparison.md")
        written.append("model_comparison.md")
    if not args.demo:
        append_history(summaries, config.EVALS_DIR / "history.jsonl")
        written.append("history.jsonl (appended)")
    partial.unlink(missing_ok=True)

    print()
    for s in summaries:
        print(f"{s['config']:48s} accuracy {_acc(s, 'field_accuracy'):22s} citations {s['citation_rate']:.1%} · "
              f"recall {s['flag_recall']:.0%} · escalated {s['escalation_rate']:.0%} · "
              f"${s['cost_per_document_usd']:.4f}/doc · p50 {s['latency_p50_s']:.1f}s · cache {s['cache_hit_rate']:.0%}")
    print("wrote " + ", ".join(f"evals/{w}" for w in written))
    return summaries


if __name__ == "__main__":
    main()
