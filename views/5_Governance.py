import streamlit as st

from reload_guard import ensure_fresh

ensure_fresh()  # load current bidlens code after a redeploy (see reload_guard.py)

from bidlens import config, ui

ui.setup_page("Governance")
ui.sidebar()

ui.page_header("Governance", ui.PAGE_ICON["views/5_Governance.py"],
               "Solution inventory entry, controls, and limitations, kept in version control alongside the code.")

st.markdown(ui.md((config.DOCS_DIR / "GOVERNANCE.md").read_text(encoding="utf-8")))  # "$0.01 ... $0.03" is not math

with st.expander("Current extraction prompt"):
    st.code((config.PROMPTS_DIR / f"{config.EXTRACT_PROMPT_VERSION}.md").read_text(encoding="utf-8"),
            language="markdown")
with st.expander("Business assumptions in effect"):
    st.markdown(
        f"""
- Cost of capital for payment-term valuation: **{config.COST_OF_CAPITAL:.0%}**
- Price outlier threshold: **±{config.PRICE_OUTLIER_THRESHOLD:.0%}** vs. peer median
- Tariff exposure flag: **≥{config.HIGH_TARIFF_THRESHOLD:.0%}**
- Incoterms treated as freight-included: {", ".join(sorted(config.FREIGHT_INCLUDED_INCOTERMS))}
- Time baseline: **{config.MANUAL_MINUTES_PER_QUOTE} min** manual vs **{config.ASSISTED_MINUTES_PER_QUOTE} min** assisted per quote
- Tariff, freight, and FX tables: `data/reference/` (illustrative values)
"""
    )
