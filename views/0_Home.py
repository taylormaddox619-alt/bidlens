import streamlit as st

from reload_guard import ensure_fresh

ensure_fresh()  # load current bidlens code after a redeploy (see reload_guard.py)

from bidlens import ui

ui.setup_page("Home", "📑")
ctx = ui.sidebar()

st.title("📑 BidLens")
st.subheader("AI-assisted supplier bid analysis, with a buyer in control")

st.markdown(
    """
Comparing supplier quotes is repetitive, error-prone work. Every supplier formats quotes differently: different
currencies, Incoterms, price breaks, payment terms and fine print. Buyers re-key it all into a spreadsheet, and the
**lowest unit price often isn't the lowest landed cost** once freight, tariffs and terms are included.

BidLens automates the tactical steps and keeps judgment calls with the buyer:
"""
)

cols = st.columns(5)
steps = [
    ("1 · Extract", "Claude reads each PDF or Excel quote into a fixed schema. **Every value cites the exact text** it came from."),
    ("2 · Check", "Deterministic business rules flag exceptions: expired quotes, lead-time misses, tariff exposure, "
                  "prepayment, missing warranty, fabricated citations and prompt-injection text."),
    ("3 · Review", "**Human-in-the-loop:** the buyer verifies, edits and approves each quote. "
                   "Comparison stays locked until every quote is reviewed."),
    ("4 · Compare", "Quotes are normalized to **total landed cost** (FX, freight, duty, tooling, terms) and "
                    "scored with adjustable weights and a visible rationale."),
    ("5 · Decide & measure", "Draft award memo, recorded decision and audit log. Adoption and "
                             "outcomes roll up to an **AI Scorecard**."),
]
for col, (title, body) in zip(cols, steps):
    with col:
        st.markdown(f"**{title}**")
        st.caption(body)

st.divider()
left, right = st.columns([3, 2])
with left:
    st.markdown("#### Try the demo in 2 minutes")
    st.markdown(
        """
1. **New Bid Event → Quick start**: click *Load demo scenario*, or *🎲 Generate a fresh scenario* for suppliers nobody has seen.
2. Follow the **step tracker** at the top of each page. The blue **Next** button always takes you to the next step.
3. **Review & Approve**: quotes open highest risk first. 🔴 **Red boxes** are high-risk issues and 🟠 **amber boxes** are medium risk, each with what to do. Fix values, then approve or reject.
4. **Comparison**: see why the lowest unit price loses on landed cost, check red flags by supplier, and record the award.
5. **AI Scorecard** and **Governance**: see how the tool is measured and controlled.
"""
    )
    ui.nav_link("pages/1_New_Bid_Event.py", "Start: New Bid Event", "➡️")
with right:
    st.markdown("#### Built with")
    st.markdown(
        """
- **Claude** (Anthropic API) with schema-constrained structured outputs
- **Python + Streamlit** UI, **DuckDB** store (Snowflake-portable SQL)
- **pytest** unit tests + an **evaluation harness** against labeled ground truth
- Developed with **Claude Code**, version-controlled on **GitHub**
"""
    )
    mode_note = "Live (Claude API)" if ctx["mode"] == "live" else "Demo (recorded extractions, no API calls)"
    st.info(f"Current mode: **{mode_note}**")

st.caption("All suppliers, prices, tariff and FX rates in this demo are fictional or illustrative. "
           "Not affiliated with any company.")
