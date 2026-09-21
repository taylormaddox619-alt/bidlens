import json

import altair as alt
import pandas as pd
import streamlit as st

from reload_guard import ensure_fresh

ensure_fresh()  # load current bidlens code after a redeploy (see reload_guard.py)

from bidlens import config, db, ui
from bidlens.schemas import FIELD_SPECS

ui.setup_page("AI Scorecard")
ui.sidebar()

ui.page_header("AI Scorecard", ui.PAGE_ICON["views/4_AI_Scorecard.py"],
               "Tracks adoption and operating results, not just activity. Figures reflect usage of this app instance.")



def card(column, label: str, value, **kwargs) -> None:
    """A metric as a bordered card, so each row of figures reads as a group rather than loose numbers."""
    column.metric(label, value, border=True, **kwargs)


def detail_summary(raw: str) -> str:
    """The part of an audit entry's JSON detail a person wants to read: the file, the note, or the fields."""
    try:
        detail = json.loads(raw or "{}")
    except ValueError:
        return raw or ""
    if not isinstance(detail, dict):
        return str(detail)
    for key in ("filename", "note", "reason", "event_name", "supplier"):
        if detail.get(key):
            return str(detail[key])
    if detail.get("fields"):
        return ", ".join(detail["fields"])
    if "documents" in detail:
        return f"{detail['documents']} documents"
    return ""


data = db.scorecard_data()
events, quotes, edits, runs, awards = (data[k] for k in ("events", "quotes", "edits", "runs", "awards"))

if not quotes:
    st.info("No activity yet. Run the demo scenario to populate the scorecard.")
    st.stop()

extracted = [q for q in quotes if q["extraction_json"]]
reviewed = [q for q in quotes if q["status"] in ("approved", "rejected")]
approved = [q for q in quotes if q["status"] == "approved"]
extract_runs = [r for r in runs if r["purpose"] == "extract"]
live_runs = [r for r in runs if r["source"] == "live"]

fields_per_quote = len(FIELD_SPECS)
total_fields = len(extracted) * fields_per_quote
edited_quotes = {e["quote_id"] for e in edits}
edit_rate = len([e for e in edits if e["field"] not in ("price_tiers", "supplier_exceptions")]) / total_fields if total_fields else 0
flagged_high = sum(any(f["severity"] == "high" for f in json.loads(q["flags_json"] or "[]")) for q in quotes)
hours_saved = len(reviewed) * (config.MANUAL_MINUTES_PER_QUOTE - config.ASSISTED_MINUTES_PER_QUOTE) / 60
cost_avoided = sum(a["savings_vs_naive_usd"] or 0 for a in awards)
live_cost = sum(r["cost_usd"] or 0 for r in live_runs)  # everything: extraction, escalation, memos, test-quote writing
live_extract_runs = [r for r in live_runs if r["purpose"] in ("extract", "extract_escalation")]
extract_cost = sum(r["cost_usd"] or 0 for r in live_extract_runs)

st.markdown("#### Adoption")
a1, a2, a3, a4 = st.columns(4)
card(a1, "Bid events", len(events))
card(a2, "Quotes processed", len(quotes))
card(a3, "Quotes reviewed by a buyer", len(reviewed))
card(a4, "Awards recorded", len(awards))

st.markdown("#### Quality")
q1, q2, q3, q4 = st.columns(4)
card(q1, "Extraction success", f"{len(extracted) / len(quotes):.0%}", help="Quotes with a valid structured extraction")
card(q2, "Field edit rate", f"{edit_rate:.1%}", help="Share of extracted fields a buyer corrected. Lower = more accurate")
card(q3, "Quotes needing a correction", f"{len(edited_quotes)} / {len(extracted)}")
card(q4, "Quotes with high-severity exceptions", f"{flagged_high} / {len(quotes)}")

eval_path = config.EVALS_DIR / "results.json"
ev = json.loads(eval_path.read_text(encoding="utf-8")) if eval_path.exists() else None
if ev and "fixture" in ev["source"]:
    ev = None  # replayed hand-labeled data says nothing about model accuracy
if ev is None:
    st.caption("Offline evaluation: no live model evaluation recorded yet (`python evals/run_evals.py`).")
if ev:
    st.markdown("#### Offline evaluation (labeled ground truth)")
    e1, e2, e3, e4 = st.columns(4)
    card(e1, "Field accuracy", f"{ev['field_accuracy']:.1%}")
    card(e2, "Citations verified", f"{ev['citation_rate']:.1%}")
    card(e3, "Exception recall", f"{ev['flag_recall']:.0%}")
    card(e4, "Exception precision", f"{ev['flag_precision']:.0%}")
    if "latency_p50_s" in ev:  # written by the multi-trial harness
        f1, f2, f3, f4 = st.columns(4)
        card(f1, "Cost per document", f"${ev['cost_per_document_usd']:.4f}")
        card(f2, "Latency p50 / p95", f"{ev['latency_p50_s']:.1f} s / {ev['latency_p95_s']:.1f} s",
                  help=f"Over {ev['n']} document runs ({ev['documents']} documents × {ev['trials']} trials)")
        card(f3, "Prompt cache hit rate", f"{ev['cache_hit_rate']:.0%}",
                  help="Share of input tokens served from the prompt cache (0.1× the input price)")
        g = ev["gate"]
        fired = g["fired_wrong"] + g["fired_ok"]
        card(f4, "Quality gate fired", f"{fired} / {g['n']}",
                  help=f"Escalation trigger. Caught {g['fired_wrong']} wrong extraction(s), missed {g['quiet_wrong']}, "
                       f"fired needlessly {g['fired_ok']} time(s).")
    spread = ev.get("field_accuracy_spread")
    trials = f" × {ev['trials']} trials (accuracy {spread['min']:.1%}–{spread['max']:.1%})" \
        if spread and ev.get("trials", 1) > 1 else ""
    st.caption(f"Run {ev['run_at']} · source: {ev['source']} · model {ev['model']} · prompt {ev['prompt_version']}"
               + (f" · effort {ev['effort']}" if ev.get("effort") else "")
               + f" · {ev['documents']} documents{trials}. See evals/report.md and evals/model_comparison.md.")

history_path = config.EVALS_DIR / "history.jsonl"
if history_path.exists() and history_path.stat().st_size:
    hist = pd.DataFrame([json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines() if line])
    hist["run_at"] = pd.to_datetime(hist["run_at"].str.replace(" UTC", ""), utc=True)
    with st.expander(f"Eval history · {len(hist)} configuration runs", expanded=False):
        base = alt.Chart(hist).encode(x=alt.X("run_at:T", title="Run"), color=alt.Color("config:N", title="Configuration"),
                                      tooltip=["run_at:T", "config:N", "git:N", alt.Tooltip("field_accuracy:Q", format=".1%"),
                                               alt.Tooltip("cost_per_document_usd:Q", format="$.4f"),
                                               alt.Tooltip("latency_p50_s:Q", format=".1f"), "trials:Q"])
        acc = base.mark_line(point=True).encode(y=alt.Y("field_accuracy:Q", title="Field accuracy",
                                                        axis=alt.Axis(format="%"), scale=alt.Scale(domain=[0.8, 1.0])))
        cost = base.mark_line(point=True).encode(y=alt.Y("cost_per_document_usd:Q", title="Cost / document (USD)"))
        st.altair_chart(alt.hconcat(acc, cost).resolve_scale(color="shared"), width="stretch")
        st.caption("One point per configuration per live eval run. Each point is the mean over that run's trials.")

st.markdown("#### Business value")
b1, b2, b3, b4 = st.columns(4)
card(b1, "Buyer hours saved (est.)", f"{hours_saved:.1f}",
          help=f"Reviewed quotes × ({config.MANUAL_MINUTES_PER_QUOTE} min manual − "
               f"{config.ASSISTED_MINUTES_PER_QUOTE} min assisted). Baselines are assumptions to validate with users.")
card(b2, "Cost avoided vs. lowest unit price", ui.money(cost_avoided),
          help="Landed cost of the lowest-unit-price bid minus landed cost of the awarded bid")
card(b3, "Live API spend", f"${live_cost:.2f}")
live_quotes = {r["quote_id"] for r in live_extract_runs}
card(b4, "API cost per quote", f"${extract_cost / len(live_quotes):.3f}" if live_quotes else "n/a (demo)",
          help="Extraction calls for a quote, including escalations, divided by quotes processed live. Memo drafts "
               "and AI-generated test quotes count toward Live API spend but not here.")

followed = [a for a in awards if a["followed_recommendation"]]
if awards:
    st.caption(f"Buyers followed the top-scored recommendation in {len(followed)} of {len(awards)} awards; "
               "overrides require a written justification.")

st.markdown("#### Model routing")
first_pass = [r for r in live_runs if r["purpose"] == "extract"]
escalations = [r for r in live_runs if r["purpose"] == "extract_escalation"]
r1, r2 = st.columns([1, 2])
card(r1, "Extractions escalated to stronger model",
          f"{len(escalations) / len(first_pass):.0%}" if first_pass else "n/a (demo)",
          help="Share of first-pass extractions that failed automatic checks (missing citations, "
               "low confidence on required fields, schema failure) and were re-run on the escalation model")
with r2:
    if live_runs:
        lr = pd.DataFrame(live_runs)
        for col in ("cache_read_tokens", "cache_write_tokens", "input_tokens", "output_tokens"):
            lr[col] = pd.to_numeric(lr.get(col), errors="coerce").fillna(0)  # rows logged before caching existed
        by_model = (lr.groupby("model")
                    .agg(calls=("id", "count"), cost_usd=("cost_usd", "sum"), p50_s=("latency_s", "median"),
                         p95_s=("latency_s", lambda s: s.quantile(0.95)),
                         cache_hit=("cache_read_tokens", "sum"), uncached=("input_tokens", "sum"),
                         cache_write=("cache_write_tokens", "sum"))
                    .reset_index())
        denom = by_model["cache_hit"] + by_model["uncached"] + by_model["cache_write"]
        by_model["cache_hit_rate"] = (by_model["cache_hit"] / denom.where(denom > 0)).fillna(0)
        st.dataframe(by_model[["model", "calls", "cost_usd", "p50_s", "p95_s", "cache_hit_rate"]],
                     hide_index=True, width="stretch",
                     column_config={"cost_usd": st.column_config.NumberColumn("Spend", format="$%.4f"),
                                    "p50_s": st.column_config.NumberColumn("Latency p50", format="%.1f s"),
                                    "p95_s": st.column_config.NumberColumn("Latency p95", format="%.1f s"),
                                    "cache_hit_rate": st.column_config.NumberColumn("Cache hit", format="percent")})
        # Where the money goes: cached prefix tokens are priced at 1.25x (write) and 0.1x (read) the input rate.
        prices = lr["model"].map(lambda m: config.MODEL_PRICING.get(m, max(config.MODEL_PRICING.values())))
        p_in, p_out = prices.map(lambda p: p[0]), prices.map(lambda p: p[1])
        split = pd.DataFrame({
            "Uncached input": (lr["input_tokens"] * p_in).sum(),
            "Cache write": (lr["cache_write_tokens"] * p_in * config.CACHE_WRITE_MULTIPLIER).sum(),
            "Cache read": (lr["cache_read_tokens"] * p_in * config.CACHE_READ_MULTIPLIER).sum(),
            "Output": (lr["output_tokens"] * p_out).sum(),
        }, index=["usd"]).T.reset_index().rename(columns={"index": "component"})
        split["usd"] /= 1_000_000
        total = split["usd"].sum()
        split["share"] = split["usd"] / total if total else 0.0
        st.dataframe(split, hide_index=True, width="stretch",
                     column_config={"component": "Cost component", "usd": st.column_config.NumberColumn("Spend", format="$%.4f"),
                                    "share": st.column_config.NumberColumn("Share", format="percent")})
        st.caption(f"Spend by component across {len(lr)} live model calls. Output tokens usually dominate, "
                   "which is why reasoning effort is the largest cost lever and prompt caching a smaller one.")
    else:
        routes = config.MODEL_ROUTES
        st.caption(f"Routing: extraction on `{routes['extract']}`, escalating to `{routes['extract_escalation']}` "
                   f"when checks fail; memos on `{routes['memo']}`. Spend by model appears after live runs.")

st.markdown("#### Model runs")
TIME_COLUMN = st.column_config.DatetimeColumn("Time (UTC)", format="YYYY-MM-DD HH:mm")
if extract_runs or runs:
    df = pd.DataFrame(runs)[["ts", "purpose", "source", "model", "prompt_version", "input_tokens",
                             "output_tokens", "cost_usd", "latency_s", "status", "error"]]
    text_cols = ["purpose", "source", "model", "prompt_version", "status", "error"]
    df[text_cols] = df[text_cols].fillna("")  # numeric columns keep NaN so their number formats still apply
    st.dataframe(df.sort_values("ts", ascending=False), hide_index=True, width="stretch",
                 column_config={"ts": TIME_COLUMN, "purpose": "Purpose", "source": "Source", "model": "Model",
                                "prompt_version": "Prompt", "input_tokens": "In tokens",
                                "output_tokens": "Out tokens",
                                "cost_usd": st.column_config.NumberColumn("Cost", format="$%.4f"),
                                "latency_s": st.column_config.NumberColumn("Latency", format="%.1f s"),
                                "status": "Status", "error": "Error"})

st.markdown("#### Audit log")
log = pd.DataFrame(db.audit_log(limit=300))
if not log.empty:
    log = log[["ts", "actor", "action", "event_id", "quote_id", "detail"]].copy()
    log["detail"] = log["detail"].map(detail_summary)
    log["action"] = log["action"].str.replace("_", " ")
    st.dataframe(log.fillna(""), hide_index=True, width="stretch",
                 column_config={"ts": TIME_COLUMN, "actor": "Who", "action": "Action", "event_id": "Event",
                                "quote_id": "Quote", "detail": "Detail"})
