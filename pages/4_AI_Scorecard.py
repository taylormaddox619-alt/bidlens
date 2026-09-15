import json

import pandas as pd
import streamlit as st

from reload_guard import ensure_fresh

ensure_fresh()  # load current bidlens code after a redeploy (see reload_guard.py)

from bidlens import config, db, ui

ui.setup_page("AI Scorecard", "📊")
ui.sidebar()

st.title("📊 AI Scorecard")
st.caption("Tracks adoption and operating results, not just activity. Figures reflect usage of this app instance.")

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

fields_per_quote = 17
total_fields = len(extracted) * fields_per_quote
edited_quotes = {e["quote_id"] for e in edits}
edit_rate = len([e for e in edits if e["field"] not in ("price_tiers", "supplier_exceptions")]) / total_fields if total_fields else 0
flagged_high = sum(any(f["severity"] == "high" for f in json.loads(q["flags_json"] or "[]")) for q in quotes)
hours_saved = len(reviewed) * (config.MANUAL_MINUTES_PER_QUOTE - config.ASSISTED_MINUTES_PER_QUOTE) / 60
cost_avoided = sum(a["savings_vs_naive_usd"] or 0 for a in awards)
live_cost = sum(r["cost_usd"] or 0 for r in live_runs)

st.markdown("#### Adoption")
a1, a2, a3, a4 = st.columns(4)
a1.metric("Bid events", len(events))
a2.metric("Quotes processed", len(quotes))
a3.metric("Quotes reviewed by a buyer", len(reviewed))
a4.metric("Awards recorded", len(awards))

st.markdown("#### Quality")
q1, q2, q3, q4 = st.columns(4)
q1.metric("Extraction success", f"{len(extracted) / len(quotes):.0%}", help="Quotes with a valid structured extraction")
q2.metric("Field edit rate", f"{edit_rate:.1%}", help="Share of extracted fields a buyer corrected. Lower = more accurate")
q3.metric("Quotes needing a correction", f"{len(edited_quotes)} / {len(extracted)}")
q4.metric("Quotes with high-severity exceptions", f"{flagged_high} / {len(quotes)}")

eval_path = config.EVALS_DIR / "results.json"
ev = json.loads(eval_path.read_text(encoding="utf-8")) if eval_path.exists() else None
if ev and "fixture" in ev["source"]:
    ev = None  # replayed hand-labeled data says nothing about model accuracy
if ev is None:
    st.caption("Offline evaluation: no live model evaluation recorded yet (`python evals/run_evals.py`).")
if ev:
    st.markdown("#### Offline evaluation (labeled ground truth)")
    e1, e2, e3, e4 = st.columns(4)
    e1.metric("Field accuracy", f"{ev['field_accuracy']:.1%}")
    e2.metric("Citations verified", f"{ev['citation_rate']:.1%}")
    e3.metric("Exception recall", f"{ev['flag_recall']:.0%}")
    e4.metric("Exception precision", f"{ev['flag_precision']:.0%}")
    st.caption(f"Run {ev['run_at']} · source: {ev['source']} · model {ev['model']} · prompt {ev['prompt_version']} · "
               f"{ev['documents']} documents. See evals/report.md.")

st.markdown("#### Business value")
b1, b2, b3, b4 = st.columns(4)
b1.metric("Buyer hours saved (est.)", f"{hours_saved:.1f}",
          help=f"Reviewed quotes × ({config.MANUAL_MINUTES_PER_QUOTE} min manual − "
               f"{config.ASSISTED_MINUTES_PER_QUOTE} min assisted). Baselines are assumptions to validate with users.")
b2.metric("Cost avoided vs. lowest unit price", ui.money(cost_avoided),
          help="Landed cost of the lowest-unit-price bid minus landed cost of the awarded bid")
b3.metric("Live API spend", f"${live_cost:.2f}")
live_quotes = {r["quote_id"] for r in live_runs if r["purpose"] in ("extract", "extract_escalation")}
b4.metric("API cost per quote", f"${live_cost / len(live_quotes):.3f}" if live_quotes else "n/a (demo)",
          help="All model calls for a quote, including escalations, divided by quotes processed live")

followed = [a for a in awards if a["followed_recommendation"]]
if awards:
    st.caption(f"Buyers followed the top-scored recommendation in {len(followed)} of {len(awards)} awards; "
               "overrides require a written justification.")

st.markdown("#### Model routing")
first_pass = [r for r in live_runs if r["purpose"] == "extract"]
escalations = [r for r in live_runs if r["purpose"] == "extract_escalation"]
r1, r2 = st.columns([1, 2])
r1.metric("Extractions escalated to stronger model",
          f"{len(escalations) / len(first_pass):.0%}" if first_pass else "n/a (demo)",
          help="Share of first-pass extractions that failed automatic checks (missing citations, "
               "low confidence on required fields, schema failure) and were re-run on the escalation model")
with r2:
    if live_runs:
        by_model = (pd.DataFrame(live_runs).groupby("model")
                    .agg(calls=("id", "count"), cost_usd=("cost_usd", "sum"), avg_latency_s=("latency_s", "mean"))
                    .reset_index())
        st.dataframe(by_model, hide_index=True, width="stretch",
                     column_config={"cost_usd": st.column_config.NumberColumn("Spend", format="$%.4f"),
                                    "avg_latency_s": st.column_config.NumberColumn("Avg latency", format="%.1f s")})
    else:
        routes = config.MODEL_ROUTES
        st.caption(f"Routing: extraction on `{routes['extract']}`, escalating to `{routes['extract_escalation']}` "
                   f"when checks fail; memos on `{routes['memo']}`. Spend by model appears after live runs.")

st.markdown("#### Model runs")
if extract_runs or runs:
    df = pd.DataFrame(runs)[["ts", "purpose", "source", "model", "prompt_version", "input_tokens",
                             "output_tokens", "cost_usd", "latency_s", "status", "error"]]
    st.dataframe(df.sort_values("ts", ascending=False), hide_index=True, width="stretch",
                 column_config={"cost_usd": st.column_config.NumberColumn(format="$%.4f"),
                                "latency_s": st.column_config.NumberColumn(format="%.1f s")})

st.markdown("#### Audit log")
log = pd.DataFrame(db.audit_log(limit=300))
if not log.empty:
    st.dataframe(log[["ts", "actor", "action", "event_id", "quote_id", "detail"]], hide_index=True, width="stretch")
