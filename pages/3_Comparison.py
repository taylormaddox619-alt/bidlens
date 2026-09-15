import altair as alt
import pandas as pd
import streamlit as st

from reload_guard import ensure_fresh

ensure_fresh()  # load current bidlens code after a redeploy (see reload_guard.py)

from bidlens import db, memo, ui, workflow
from bidlens.reference import fx_as_of
from bidlens.rules import label
from bidlens.scoring import DEFAULT_WEIGHTS

ui.setup_page("Comparison", "⚖️")
ctx = ui.sidebar()

st.title("⚖️ Bid Comparison")

event_id = ctx["event_id"]
if not event_id:
    st.info("Create or load a bid event first.")
    st.stop()

event, rfq = ui.load_rfq(event_id)
quotes = db.list_quotes(event_id)
pending = [q for q in quotes if q["status"] not in ("approved", "rejected")]
approved = [q for q in quotes if q["status"] == "approved"]

# Guardrail: the comparison only uses buyer-verified data.
if not quotes or pending:
    st.warning(f"🔒 Comparison is locked until every quote is reviewed. "
               f"{len(pending)} quote(s) still need approval or rejection.")
    ui.nav_link("pages/2_Review_Approve.py", "Go to Review & Approve", "🔎")
    st.stop()
if len(approved) < 2:
    st.warning("At least two approved quotes are needed for a comparison.")
    st.stop()

with st.sidebar:
    st.markdown("#### Scoring weights")
    weights = {
        "cost": st.slider("Landed cost", 0, 100, DEFAULT_WEIGHTS["cost"], 5),
        "lead_time": st.slider("Lead time", 0, 100, DEFAULT_WEIGHTS["lead_time"], 5),
        "terms": st.slider("Commercial terms", 0, 100, DEFAULT_WEIGHTS["terms"], 5),
        "risk": st.slider("Risk (open flags)", 0, 100, DEFAULT_WEIGHTS["risk"], 5),
    }
    if sum(weights.values()) == 0:
        st.error("At least one weight must be above zero.")
        st.stop()

rows = workflow.comparison(event_id, rfq, weights)
best = rows[0]
naive = min(rows, key=lambda r: r["landed"].unit_price_usd)
cheapest_landed = min(rows, key=lambda r: r["landed"].landed_total_usd)
naive_gap = naive["landed"].landed_total_usd - cheapest_landed["landed"].landed_total_usd

st.caption(f"{event['name']} · {rfq.quantity:,} units · all values in USD (FX {fx_as_of()})")

tiles = [
    ("Recommended", ui.short_name(best["supplier"]), f"Score {best['overall']:.1f} / 100"),
    ("Lowest landed cost", ui.money(cheapest_landed["landed"].landed_total_usd),
     ui.short_name(cheapest_landed["supplier"])),
    ("Cost avoided vs. lowest unit price", ui.money(naive_gap),
     f"{ui.short_name(naive['supplier'])} lands at {ui.money(naive['landed'].landed_total_usd)}"),
]
for col, (title, value, sub) in zip(st.columns(3), tiles):
    with col.container(border=True):
        st.caption(title)
        st.markdown(ui.md(f"**<span style='font-size:1.35rem;line-height:1.25'>{value}</span>**"),
                    unsafe_allow_html=True)
        st.caption(ui.md(sub))

if naive["supplier"] != cheapest_landed["supplier"]:
    st.info(ui.md(
        f"**Insight:** {naive['supplier']} has the lowest unit price "
        f"(${naive['landed'].unit_price_usd:,.2f}), but freight, duty, tooling and terms add "
        f"{ui.money(naive['landed'].landed_total_usd - naive['landed'].goods_usd)}. Its total landed cost is "
        f"{ui.money(naive_gap)} above {cheapest_landed['supplier'].rstrip('.')}."
    ))

# --- Landed cost breakdown chart ----------------------------------------------------------
st.markdown("#### Landed cost per unit, by component")
components = [("Goods", "goods_usd"), ("Tooling", "tooling_usd"), ("Freight", "freight_usd"),
              ("Duty / tariff", "duty_usd"), ("Payment terms", "terms_adjustment_usd")]
palette = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]  # reference categorical slots 1-5
order = [r["supplier"] for r in sorted(rows, key=lambda r: r["landed"].landed_per_unit_usd)]
chart_rows = []
for r in rows:
    for i, (name, attr) in enumerate(components):
        chart_rows.append({"Supplier": r["supplier"], "Component": name, "order": i,
                           "Per unit (USD)": getattr(r["landed"], attr) / rfq.quantity,
                           "Landed per unit": r["landed"].landed_per_unit_usd})
chart_df = pd.DataFrame(chart_rows)
bars = alt.Chart(chart_df).mark_bar(stroke="#fcfcfb", strokeWidth=2).encode(
    y=alt.Y("Supplier:N", sort=order, title=None, axis=alt.Axis(labelLimit=260, labelColor="#52514e")),
    x=alt.X("sum(Per unit (USD)):Q", title="USD per unit",
            scale=alt.Scale(domain=[0, max(r["landed"].landed_per_unit_usd for r in rows) * 1.18]),
            axis=alt.Axis(format="$,.0f", gridColor="#e1e0d9", labelColor="#898781", titleColor="#52514e")),
    color=alt.Color("Component:N", sort=[c for c, _ in components],
                    scale=alt.Scale(domain=[c for c, _ in components], range=palette),
                    legend=alt.Legend(orient="top", title=None, labelColor="#52514e", columns=3)),
    order=alt.Order("order:Q"),
    tooltip=[alt.Tooltip("Supplier:N"), alt.Tooltip("Component:N"),
             alt.Tooltip("Per unit (USD):Q", format="$,.2f"),
             alt.Tooltip("Landed per unit:Q", format="$,.2f")],
)
totals = alt.Chart(chart_df.drop_duplicates("Supplier")).mark_text(
    align="left", dx=6, color="#0b0b0b", fontSize=12).encode(
    y=alt.Y("Supplier:N", sort=order), x=alt.X("Landed per unit:Q"),
    text=alt.Text("Landed per unit:Q", format="$,.2f"),
)
# Fit the chart to the container width only. With Streamlit's default "fit" sizing the height
# covers legend + axis + bars, which squeezed a 2-supplier chart to zero-height bars. Here the
# height applies to the bars alone, and the legend and axis are added around them.
BAR_ROW_PX = 52
chart = (bars + totals).properties(
    height=BAR_ROW_PX * len(rows),
    autosize=alt.AutoSizeParams(type="fit-x", contains="padding"),
).configure_view(stroke=None)
st.altair_chart(chart, width="stretch")

# --- Comparison table ---------------------------------------------------------------------
st.markdown("#### Scorecard")
table = pd.DataFrame([
    {
        "Rank": i + 1,
        "Supplier": r["supplier"],
        "Score": round(r["overall"], 1),
        "Quoted unit": f"{r['landed'].unit_price_quoted:,.2f} {r['landed'].currency}",
        "Unit USD": r["landed"].unit_price_usd,
        "Tooling": r["landed"].tooling_usd,
        "Freight": r["landed"].freight_usd,
        "Freight est.": "estimated" if r["landed"].freight_estimated else "",
        "Duty": r["landed"].duty_usd,
        "Terms adj.": r["landed"].terms_adjustment_usd,
        "Landed total": r["landed"].landed_total_usd,
        "Landed / unit": r["landed"].landed_per_unit_usd,
        "Lead time (wks)": r["values"].get("lead_time_weeks"),
        "Cost": round(r["cost_score"]), "Lead": round(r["lead_time_score"]),
        "Terms": round(r["terms_score"]), "Risk": round(r["risk_score"]),
    }
    for i, r in enumerate(rows)
])
money_cols = ["Unit USD", "Tooling", "Freight", "Duty", "Terms adj.", "Landed total", "Landed / unit"]
st.dataframe(
    table, hide_index=True, width="stretch",
    column_config={c: st.column_config.NumberColumn(format="$%.2f" if "unit" in c.lower() else "$%.0f")
                   for c in money_cols}
    | {"Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f")},
)
st.download_button("Download comparison (CSV)", table.to_csv(index=False), f"{event['name']}_comparison.csv",
                   "text/csv")

with st.expander("How each score was calculated"):
    for r in rows:
        st.markdown(f"**{r['supplier']}** — overall {r['overall']:.1f}")
        st.markdown(ui.md("\n".join(f"- {line}" for line in r["rationale"] + r["landed"].notes)))
    st.caption("Cost = lowest landed ÷ this landed × 100. Lead time = fastest ÷ this × 100, −25 if it exceeds the "
               "requirement. Terms start at 100 with deductions. Risk = 100 − 20 per high flag − 8 per medium flag.")

# --- Memo & decision ----------------------------------------------------------------------
st.divider()
st.markdown("#### Award recommendation")
open_flags = [f for f in best["flags"] if f["severity"] in ("high", "medium")]
if open_flags:
    st.markdown(f"Open items on **{best['supplier']}** to close before award:")
    for f in open_flags:
        st.markdown(ui.md(f"- {ui.SEVERITY_ICON[f['severity']]} {label(f['field'])}: {f['message']}"))

memo_key = f"memo_{event_id}"
c1, c2 = st.columns(2)
if c1.button("📝 Draft memo (template)", width="stretch"):
    st.session_state[memo_key] = (memo.template_memo(rfq, rows, weights), "template")
if c2.button("✨ Draft memo with Claude", width="stretch", disabled=ctx["mode"] != "live",
             help=None if ctx["mode"] == "live" else "Requires Live mode"):
    with st.spinner("Drafting…"):
        text, meta = memo.llm_memo(rfq, rows, weights, ctx["api_key"])
    db.log_llm_run(meta, "memo", event_id)
    if text:
        st.session_state[memo_key] = (text, meta["model"])
    else:
        st.error(f"Memo generation failed: {meta.get('error')}. Use the template memo instead.")

if memo_key in st.session_state:
    text, source = st.session_state[memo_key]
    st.warning("**DRAFT: requires buyer verification.** Generated from approved data only; review before sharing.")
    with st.container(border=True):
        st.markdown(ui.md(text))
    st.caption(f"Source: {source}")
    st.download_button("Download memo (Markdown)", text, f"{event['name']}_award_memo.md", "text/markdown")

st.markdown("#### Record decision")
award = db.get_award(event_id)
if award:
    st.success(ui.md(f"Awarded to **{award['supplier']}** by {award['decided_by']} on "
                     f"{award['decided_at']:%Y-%m-%d %H:%M} UTC · cost avoided vs. lowest unit price: "
                     f"{ui.money(award['savings_vs_naive_usd'])}"))
suppliers = [r["supplier"] for r in rows]
choice = st.selectbox("Award to", suppliers, index=0)
chosen = rows[suppliers.index(choice)]
justification = ""
if choice != best["supplier"]:
    justification = st.text_area("Justification required: award differs from the highest-scoring bid")
if st.button("Record award decision", type="primary", disabled=choice != best["supplier"] and not justification.strip()):
    db.record_award({
        "event_id": event_id, "quote_id": chosen["quote_id"], "supplier": chosen["supplier"],
        "landed_total_usd": chosen["landed"].landed_total_usd, "naive_supplier": naive["supplier"],
        "naive_landed_total_usd": naive["landed"].landed_total_usd,
        "savings_vs_naive_usd": naive["landed"].landed_total_usd - chosen["landed"].landed_total_usd,
        "followed_recommendation": choice == best["supplier"], "justification": justification,
    }, ctx["actor"])
    st.rerun()
