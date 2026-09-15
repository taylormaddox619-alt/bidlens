"""Shared Streamlit helpers: mode/credentials, sidebar, and loaders."""

import os
import re

import streamlit as st

from . import config, db
from .rules import RECOMMENDED_ACTIONS, label
from .schemas import RFQ

SEVERITY = {  # icon, name, what it means for the buyer
    "high": ("🔴", "High risk", "Could make the award wrong or unsafe. Resolve it, or explicitly accept it, before approving."),
    "medium": ("🟠", "Medium risk", "Affects cost or terms. Check it and follow up with the supplier."),
    "low": ("🔵", "Info", "For awareness; no action usually needed."),
}
SEVERITY_ICON = {level: icon for level, (icon, _, _) in SEVERITY.items()}
RISK_LABEL = {"high": "🔴 High", "medium": "🟠 Medium", "none": "🟢 None"}
RISK_RANK = {"high": 0, "medium": 1, "none": 2}
STATUS_BADGE = {
    "extracted": "🟡 Needs review",
    "approved": "✅ Approved",
    "rejected": "⛔ Rejected",
    "failed": "❌ Extraction failed",
}
REVIEWED = ("approved", "rejected")
PAGES = {"setup": "pages/1_New_Bid_Event.py", "review": "pages/2_Review_Approve.py",
         "compare": "pages/3_Comparison.py", "scorecard": "pages/4_AI_Scorecard.py"}


def _secret(name: str) -> str | None:
    try:
        value = st.secrets.get(name)
    except Exception:  # no secrets.toml present
        value = None
    return value or os.environ.get(name)


def setup_page(title: str, icon: str = "📑") -> None:
    st.set_page_config(page_title=f"{title} · BidLens", page_icon=icon, layout="wide")


def sidebar() -> dict:
    """Render the shared sidebar and return the session context."""
    api_key = _secret("ANTHROPIC_API_KEY")
    passcode = _secret("BIDLENS_LIVE_PASSCODE")

    with st.sidebar:
        st.markdown("### BidLens")
        st.session_state.setdefault("actor", "demo.buyer")
        st.text_input("Your name (for the audit log)", key="actor")

        live_possible = bool(api_key)
        mode = st.radio(
            "Extraction mode",
            ["Demo", "Live"],
            key="mode_choice",
            help="Demo replays recorded extractions for the bundled sample quotes (free, instant). "
                 "Live sends documents to Claude.",
            disabled=not live_possible,
            horizontal=True,
        )
        live_unlocked = True
        if mode == "Live" and passcode:
            entered = st.text_input("Live mode passcode", type="password", key="live_passcode")
            live_unlocked = entered == passcode
            if entered and not live_unlocked:
                st.error("Incorrect passcode.")
        if not live_possible:
            st.caption("Live mode is off: no ANTHROPIC_API_KEY configured.")
        effective_mode = "live" if (mode == "Live" and live_possible and live_unlocked) else "demo"
        routes = config.MODEL_ROUTES
        st.caption(f"Extraction: `{routes['extract']}` → `{routes['extract_escalation']}` if checks fail · "
                   f"Memo: `{routes['memo']}` · prompt `{config.EXTRACT_PROMPT_VERSION}`")

        events = db.list_events()
        event_id = None
        if events:
            labels = {e["id"]: f"{e['name']} ({e['status']})" for e in events}
            ids = list(labels)
            current = st.session_state.get("event_id")
            index = ids.index(current) if current in ids else 0
            event_id = st.selectbox("Bid event", ids, index=index, format_func=labels.get)
            st.session_state["event_id"] = event_id

        st.divider()
        st.caption("⚠️ Demo app with fictional data. Do not upload confidential or real supplier documents.")

    return {"mode": effective_mode, "api_key": api_key, "actor": st.session_state["actor"] or "anonymous",
            "event_id": event_id}


def nav_link(page: str, label: str, icon: str = "➡️") -> None:
    """Page link that degrades gracefully when a page runs outside the multipage app (e.g. tests)."""
    try:
        st.page_link(page, label=label, icon=icon)
    except Exception:
        st.caption(f"{icon} {label}")


def load_rfq(event_id: str) -> tuple[dict, RFQ]:
    event = db.get_event(event_id)
    return event, RFQ.model_validate(db.event_rfq(event))


def money(value: float) -> str:
    return f"${value:,.0f}"


def md(text: str) -> str:
    """Escape dollar signs so Streamlit markdown doesn't render amounts as LaTeX math."""
    return text.replace("$", "\\$")


_LEGAL_SUFFIX = re.compile(
    r",?\s+(co\.,?\s*ltd\.?|pvt\.?\s*ltd\.?|gmbh|ag|inc\.?|s\.a\. de c\.v\.|s\.r\.l\.?|s\.p\.a\.?|llc|ltd\.?|corp\.?)$",
    re.IGNORECASE)


def display_name(supplier: str) -> str:
    """Title-case names copied from ALL-CAPS letterheads for display; stored data is unchanged."""
    return supplier.title() if supplier.isupper() else supplier


def short_name(supplier: str) -> str:
    return _LEGAL_SUFFIX.sub("", display_name(supplier)).strip()


def quote_supplier(quote: dict) -> str:
    name = ((quote.get("reviewed") or {}).get("supplier_name") or {}).get("value")
    return display_name(name) if name else quote["filename"]


# --- Risk display (pure helpers, unit-tested) ----------------------------------------------------
def flag_counts(flags: list[dict]) -> dict:
    return {level: sum(f["severity"] == level for f in flags) for level in SEVERITY}


def risk_level(flags: list[dict]) -> str:
    counts = flag_counts(flags)
    return "high" if counts["high"] else "medium" if counts["medium"] else "none"


def risk_badge(flags: list[dict], compact: bool = False) -> str:
    counts = flag_counts(flags)
    parts = [f"{SEVERITY[level][0]}{'' if compact else ' '}{counts[level]}{'' if compact else ' ' + level}"
             for level in ("high", "medium") if counts[level]]
    if not parts:
        return "🟢" if compact else "🟢 No risks flagged"
    return (" " if compact else " · ").join(parts)


# --- Workflow guidance (pure logic + rendering) --------------------------------------------------
def event_progress(quotes: list[dict], award: dict | None) -> dict:
    reviewed = sum(q["status"] in REVIEWED for q in quotes)
    return {"total": len(quotes), "reviewed": reviewed, "pending": len(quotes) - reviewed,
            "approved": sum(q["status"] == "approved" for q in quotes), "awarded": award is not None}


def next_step(quotes: list[dict], award: dict | None) -> tuple[str, str]:
    """(page key, button label) for the single most useful next action on an event."""
    p = event_progress(quotes, award)
    if not p["total"]:
        return "setup", "Next: add supplier quotes"
    if p["pending"]:
        return "review", f"Next: review {p['pending']} quote{'s' if p['pending'] != 1 else ''} →"
    if p["approved"] < 2:
        return "setup", "Next: add more quotes (2 approved needed to compare) →"
    if not p["awarded"]:
        return "compare", "Next: compare bids and record the award →"
    return "scorecard", "View the AI Scorecard →"


def next_step_button(event_id: str, current: str, key: str) -> None:
    """Primary button to the next step; hidden when the next step is the page you're already on."""
    page, text = next_step(db.list_quotes(event_id), db.get_award(event_id))
    if page == current:
        return
    if st.button(text, type="primary", key=key):
        try:
            st.switch_page(PAGES[page])
        except Exception:  # outside the multipage app (tests)
            pass


def workflow_stepper(event_id: str, current: str) -> None:
    """Where am I, what's done, and what's next, for the active bid event."""
    quotes, award = db.list_quotes(event_id), db.get_award(event_id)
    p = event_progress(quotes, award)
    high_risk = sum(risk_level(q["flags"]) == "high" for q in quotes if q["status"] not in REVIEWED)
    steps = [
        ("setup", "1 · Add quotes", p["total"] > 0,
         f"{p['total']} quote{'s' if p['total'] != 1 else ''} added" if p["total"] else "No quotes yet"),
        ("review", "2 · Review & approve", p["total"] > 0 and not p["pending"],
         ("All reviewed" if not p["pending"] else f"{p['reviewed']} of {p['total']} reviewed"
          + (f" · 🔴 {high_risk} high-risk" if high_risk else "")) if p["total"] else "Waiting for quotes"),
        ("compare", "3 · Compare & award", p["awarded"],
         f"Awarded to {short_name(award['supplier'])}" if award
         else "Ready" if p["total"] and not p["pending"] and p["approved"] >= 2 else "Locked until review is done"),
        ("scorecard", "4 · Measure", False, "Adoption, quality & value"),
    ]
    with st.container(border=True):
        for col, (key, title, done, status) in zip(st.columns(4), steps):
            icon = "✅" if done else "👉" if key == current else "⬜"
            col.markdown(f"{icon} **{title}**" if key == current else f"{icon} {title}")
            col.caption(status)
        next_step_button(event_id, current, key=f"next_top_{current}")


def render_flags(flags: list[dict]) -> None:
    """Exceptions grouped by severity in colored boxes, each with what to do about it."""
    if not flags:
        st.success("🟢 **No risks flagged.** The rules found nothing to follow up on for this quote.")
        return

    def lines(level: str) -> str:
        return "\n".join(
            f"- **{label(f['field'])}:** {md(f['message'])}  \n  ↳ *What to do:* {RECOMMENDED_ACTIONS.get(f['code'], 'Review.')}"
            for f in flags if f["severity"] == level)

    counts = flag_counts(flags)
    if counts["high"]:
        st.error(f"**🔴 High risk ({counts['high']}): resolve or explicitly accept before approving**\n\n{lines('high')}")
    if counts["medium"]:
        st.warning(f"**🟠 Medium risk ({counts['medium']}): check and follow up with the supplier**\n\n{lines('medium')}")
    if counts["low"]:
        with st.expander(f"🔵 Info ({counts['low']})"):
            st.markdown(lines("low"))


def severity_legend() -> str:
    return " · ".join(f"{icon} **{name}**: {meaning}" for icon, name, meaning in SEVERITY.values())
