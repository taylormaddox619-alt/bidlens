import altair as alt
import pandas as pd
import streamlit as st

from reload_guard import ensure_fresh

ensure_fresh()  # load current bidlens code after a redeploy (see reload_guard.py)

from bidlens import db, memo, ui, workflow
from bidlens.reference import fx_as_of
from bidlens.rules import label
from bidlens.scoring import DEFAULT_WEIGHTS

ui.setup_page("Comparison")
ctx = ui.sidebar()

ui.page_header("Bid Comparison", ui.PAGE_ICON["views/3_Comparison.py"])

event_id = ctx["event_id"]
if not event_id:
    st.info("Create or load a bid event first.")
    st.stop()

event, rfq = ui.load_rfq(event_id)
quotes = db.list_quotes(event_id)
pending = [q for q in quotes if q["status"] not in ui.REVIEWED]
approved = [q for q in quotes if q["status"] == "approved"]

ui.workflow_stepper(event_id, "compare", quotes)

# Guardrail: the comparison only uses buyer-verified data.
if not quotes:
    st.warning("**Comparison is locked:** this event has no quotes yet. Add them on New Bid Event.",
               icon=":material/lock:")
    st.stop()
if pending:
    st.warning(f"**Comparison is locked until every quote is reviewed.** "
               f"{len(pending)} quote{'s' if len(pending) != 1 else ''} still need{'' if len(pending) != 1 else 's'} "
               "a decision:", icon=":material/lock:")
    for pq in sorted(pending, key=lambda x: ui.RISK_RANK[ui.risk_level(x["flags"])]):
        st.markdown(f"- **{ui.short_name(ui.quote_supplier(pq))}** · {ui.risk_badge(pq['flags'], markdown=True)}")
    st.stop()
# Quotes approved before the review page checked for this can lack an FX rate or a usable price.
excluded = workflow.excluded_from_comparison(event_id, rfq)
if excluded:
    st.warning(ui.md(
        f"**{len(excluded)} approved quote{'s are' if len(excluded) != 1 else ' is'} left out of this comparison** "
        "because a landed cost cannot be computed. Reopen and fix, or reject:\n"
        + "\n".join(f"- **{ui.display_name(x['supplier'])}** (`{x['filename']}`): {x['reason']}" for x in excluded)),
        icon=":material/warning:")
    ui.nav_link("views/2_Review_Approve.py", "Go to Review & Approve")
if len(approved) - len(excluded) < 2:
    st.warning("**At least two approved quotes are needed for a comparison.** Reopen a rejected quote on "
               "Review & Approve, or add more quotes on New Bid Event.", icon=":material/lock:")
    st.stop()

with ctx["sidebar_slot"]:
    st.divider()
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
# Everything below identifies a row by quote id and displays its label: one spelling per supplier on this page,
# and still unique when two quotes share a supplier name.
labels = ui.comparison_labels(rows)
for r in rows:
    r["label"] = labels[r["quote_id"]]
best = rows[0]
naive = min(rows, key=lambda r: r["landed"].unit_price_usd)
cheapest_landed = min(rows, key=lambda r: r["landed"].landed_total_usd)
naive_gap = naive["landed"].landed_total_usd - cheapest_landed["landed"].landed_total_usd

st.caption(f"{event['name']} · {rfq.quantity:,} units · all values in USD (FX {fx_as_of()})")

tiles = [
    ("Recommended", best["label"], f"Score {best['overall']:.1f} / 100"),
    ("Lowest landed cost", ui.money(cheapest_landed["landed"].landed_total_usd), cheapest_landed["label"]),
    ("Cost avoided vs. lowest unit price", ui.money(naive_gap),
     f"{naive['label']} lands at {ui.money(naive['landed'].landed_total_usd)}"),
]
for col, (title, value, sub) in zip(st.columns(3), tiles):
    with col.container(border=True):
        st.caption(title)
        st.subheader(ui.md(value), anchor=False)  # a heading wraps long supplier names; st.metric would truncate them
        st.caption(ui.md(sub))

if naive["quote_id"] != cheapest_landed["quote_id"]:
    st.info(ui.md(
        f"**Insight:** {naive['label']} has the lowest unit price "
        f"(${naive['landed'].unit_price_usd:,.2f}), but freight, duty, tooling and terms add "
        f"{ui.money(naive['landed'].landed_total_usd - naive['landed'].goods_usd)}. Its total landed cost is "
        f"{ui.money(naive_gap)} above {cheapest_landed['label']}."
    ))

# --- Landed cost breakdown chart ----------------------------------------------------------
st.markdown("#### Landed cost per unit, by component")
components = [("Goods", "goods_usd"), ("Tooling", "tooling_usd"), ("Freight", "freight_usd"),
              ("Duty / tariff", "duty_usd"), ("Payment terms", "terms_adjustment_usd")]
tokens = ui.chart_tokens()  # colours for the viewer's theme: surface, ink, grid and the categorical slots
palette = tokens["categorical"]
order = [r["label"] for r in sorted(rows, key=lambda r: r["landed"].landed_per_unit_usd)]
chart_rows = []
for r in rows:
    for i, (name, attr) in enumerate(components):
        chart_rows.append({"Supplier": r["label"], "Component": name, "order": i,
                           "Per unit (USD)": getattr(r["landed"], attr) / rfq.quantity,
                           "Landed per unit": r["landed"].landed_per_unit_usd})
chart_df = pd.DataFrame(chart_rows)
bars = alt.Chart(chart_df).mark_bar(stroke=tokens["surface"], strokeWidth=2).encode(
    y=alt.Y("Supplier:N", sort=order, title=None, axis=alt.Axis(labelLimit=260, labelColor=tokens["ink_secondary"])),
    x=alt.X("sum(Per unit (USD)):Q", title="USD per unit",
            scale=alt.Scale(domain=[0, max(r["landed"].landed_per_unit_usd for r in rows) * 1.18]),
            axis=alt.Axis(format="$,.0f", gridColor=tokens["grid"], labelColor=tokens["muted"],
                          titleColor=tokens["ink_secondary"])),
    color=alt.Color("Component:N", sort=[c for c, _ in components],
                    scale=alt.Scale(domain=[c for c, _ in components], range=palette),
                    legend=alt.Legend(orient="top", title=None, labelColor=tokens["ink_secondary"], columns=5)),
    order=alt.Order("order:Q"),
    tooltip=[alt.Tooltip("Supplier:N"), alt.Tooltip("Component:N"),
             alt.Tooltip("Per unit (USD):Q", format="$,.2f"),
             alt.Tooltip("Landed per unit:Q", format="$,.2f")],
)
totals = alt.Chart(chart_df.drop_duplicates("Supplier")).mark_text(
    align="left", dx=6, color=tokens["ink"], fontSize=12).encode(
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
        "Supplier": r["label"],
        "Risk flags": ui.risk_badge(r["flags"]),
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
        "Terms": round(r["terms_score"]), "Risk score": round(r["risk_score"]),
    }
    for i, r in enumerate(rows)
])
# The page shows the ranking: eight columns that fit without scrolling sideways. The cost components and
# sub-scores behind it are in the expander below, and the CSV carries every column.
RANKING_COLS = ["Rank", "Supplier", "Risk flags", "Score", "Quoted unit", "Landed / unit", "Landed total",
                "Lead time (wks)"]
money = {c: st.column_config.NumberColumn(format="dollar")  # "$103,569.00": thousands separators, unlike "$%.0f"
         for c in ("Unit USD", "Tooling", "Duty", "Terms adj.", "Landed total", "Landed / unit")}
st.dataframe(table[RANKING_COLS], hide_index=True, width="stretch",
             column_config=money | {"Score": st.column_config.ProgressColumn(min_value=0, max_value=100,
                                                                             format="%.1f")})
st.download_button("Download comparison (CSV)", table.to_csv(index=False), f"{event['name']}_comparison.csv",
                   "text/csv", icon=":material/download:")

st.markdown("#### Red flags by supplier")
by_risk = sorted(rows, key=lambda r: ui.RISK_RANK[ui.risk_level(r["flags"])])
st.dataframe(pd.DataFrame([
    {"Risk": ui.RISK_LABEL[ui.risk_level(r["flags"])], "Supplier": r["label"],
     "🔴 High": ui.flag_counts(r["flags"])["high"], "🟠 Medium": ui.flag_counts(r["flags"])["medium"]}
    for r in by_risk]), hide_index=True, width="stretch")
# The issues themselves as text below the table: a table cell would clip them to one line.
for r in by_risk:
    highs = [f for f in r["flags"] if f["severity"] == "high"]
    if highs:
        st.markdown(ui.md(f"**{r['label']}**: high-risk issues accepted at review\n"
                          + "\n".join(f"- {label(f['field'])}: {f['message']}" for f in highs)))
st.caption("These quotes were approved with their flags visible. High-risk items still need follow-up before award. "
           + ui.severity_legend())

with st.expander("How each score was calculated"):
    components_table = table[["Supplier", "Unit USD", "Tooling", "Freight", "Duty", "Terms adj.", "Cost", "Lead",
                              "Terms", "Risk score"]].copy()
    components_table["Freight"] = [
        f"${r['landed'].freight_usd:,.0f}" + (" (est.)" if r["landed"].freight_estimated else "") for r in rows]
    st.dataframe(components_table, hide_index=True, width="stretch",
                 column_config=money | {"Cost": "Cost score", "Lead": "Lead score", "Terms": "Terms score"})
    for r in rows:
        st.markdown(f"**{r['label']}** — overall {r['overall']:.1f}")
        st.markdown(ui.md("\n".join(f"- {line}" for line in r["rationale"] + r["landed"].notes)))
    st.caption("Cost = lowest landed ÷ this landed × 100. Lead time = fastest ÷ this × 100, −25 if it exceeds the "
               "requirement. Terms start at 100 with deductions. Risk = 100 − 20 per high flag − 8 per medium flag.")

# --- Memo & decision ----------------------------------------------------------------------
st.divider()
st.markdown(f"#### Award recommendation: {best['label']} · {ui.risk_badge(best['flags'], markdown=True)}")
if any(f["severity"] == "high" for f in best["flags"]):
    st.error("**The highest-scoring supplier has high-risk issues.** Close them, or choose another supplier "
             "with a written justification, before recording the award.", icon=":material/error:")
ui.render_flags([f for f in best["flags"] if f["severity"] in ("high", "medium")])

memo_key = f"memo_{event_id}"
c1, c2 = st.columns(2)
if c1.button("Draft memo (template)", width="stretch", icon=":material/description:"):
    st.session_state[memo_key] = (memo.template_memo(rfq, rows, weights), "template")
if c2.button("Draft memo with Claude", width="stretch", disabled=ctx["mode"] != "live", icon=":material/auto_awesome:",
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
choice = st.selectbox("Award to", [r["quote_id"] for r in rows], index=0, format_func=labels.get)
chosen = next(r for r in rows if r["quote_id"] == choice)
followed = chosen["quote_id"] == best["quote_id"]
justification = ""
if not followed:
    justification = st.text_area("Justification required: award differs from the highest-scoring bid")
if st.button("Record award decision", type="primary", disabled=not followed and not justification.strip(),
             icon=":material/gavel:"):
    db.record_award({
        "event_id": event_id, "quote_id": chosen["quote_id"], "supplier": chosen["supplier"],
        "landed_total_usd": chosen["landed"].landed_total_usd, "naive_supplier": naive["supplier"],
        "naive_landed_total_usd": naive["landed"].landed_total_usd,
        "savings_vs_naive_usd": naive["landed"].landed_total_usd - chosen["landed"].landed_total_usd,
        "followed_recommendation": followed, "justification": justification,
    }, ctx["actor"])
    st.rerun()
if award:
    ui.next_step_button(quotes, award, "compare", key="next_bottom_compare")
