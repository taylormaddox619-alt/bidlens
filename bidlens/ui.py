"""Shared Streamlit helpers: mode/credentials, sidebar, and loaders."""

import os
import re

import streamlit as st

from . import config, db
from .schemas import RFQ

SEVERITY_ICON = {"high": "🔴", "medium": "🟠", "low": "🔵"}
STATUS_BADGE = {
    "extracted": "🟡 Needs review",
    "approved": "✅ Approved",
    "rejected": "⛔ Rejected",
    "failed": "❌ Extraction failed",
}


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
        st.caption(f"Model: `{config.DEFAULT_MODEL}` · prompt `{config.EXTRACT_PROMPT_VERSION}`")

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


_LEGAL_SUFFIX = re.compile(r",?\s+(co\.,?\s*ltd\.?|gmbh|inc\.?|s\.a\. de c\.v\.|llc|ltd\.?|corp\.?)$", re.IGNORECASE)


def short_name(supplier: str) -> str:
    return _LEGAL_SUFFIX.sub("", supplier).strip()
