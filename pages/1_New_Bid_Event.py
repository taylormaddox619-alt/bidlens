import json
from datetime import date

import pandas as pd
import streamlit as st

from bidlens import db, ui, workflow
from bidlens.config import SAMPLES_DIR
from bidlens.ingest import SUPPORTED_TYPES

ui.setup_page("New Bid Event", "🆕")
ctx = ui.sidebar()

st.title("🆕 New Bid Event")
st.caption("Create an RFQ, then upload the supplier quotes you received.")


def run_documents(event_id: str, files: list[tuple[str, bytes]]) -> None:
    _, rfq = ui.load_rfq(event_id)
    results = []
    progress = st.progress(0.0, text="Extracting…")
    for i, (name, data) in enumerate(files, start=1):
        progress.progress((i - 1) / len(files), text=f"Extracting {name}…")
        results.append(workflow.process_document(event_id, rfq, name, data, ctx["mode"], ctx["api_key"], ctx["actor"]))
    workflow.refresh_flags(event_id, rfq)
    progress.progress(1.0, text="Done")
    st.session_state["last_results"] = results


with st.expander("⚡ Quick start: load the demo scenario", expanded=not db.list_events()):
    st.markdown(
        "Creates **RFQ-2026-0142**: 500 cast-iron compressor air-end housings, 12-week lead time requirement, "
        "Net 60 standard terms. It loads **4 fictional supplier quotes** (3 PDFs, 1 Excel), each with a built-in problem to catch."
    )
    if st.button("Load demo scenario", type="primary"):
        rfq = json.loads((SAMPLES_DIR / "demo_rfq.json").read_text(encoding="utf-8"))
        event_id = db.create_event(rfq, ctx["actor"])
        st.session_state["event_id"] = event_id
        files = [(p.name, p.read_bytes()) for p in sorted(SAMPLES_DIR.iterdir()) if p.suffix in (".pdf", ".xlsx")]
        run_documents(event_id, files)
        st.rerun()

with st.expander("Create a custom bid event"):
    with st.form("new_event"):
        c1, c2 = st.columns(2)
        name = c1.text_input("Event name", placeholder="RFQ-2026-0150 Motor mounts")
        item = c2.text_input("Item / scope", placeholder="Steel motor mount bracket, drawing MM-220")
        c3, c4, c5 = st.columns(3)
        quantity = c3.number_input("Quantity", min_value=1, value=1000, step=50)
        lead = c4.number_input("Required lead time (weeks)", min_value=1.0, value=12.0, step=1.0)
        terms = c5.number_input("Standard payment terms (days)", min_value=0, value=60, step=15)
        c6, c7, c8 = st.columns(3)
        currency = c6.selectbox("RFQ currency", ["USD"])
        destination = c7.text_input("Ship-to", value="Charlotte, NC distribution center")
        eval_date = c8.date_input("Evaluation date", value=date.today())
        if st.form_submit_button("Create event"):
            if not name or not item:
                st.error("Event name and item are required.")
            else:
                event_id = db.create_event(
                    {"event_name": name, "item": item, "quantity": int(quantity), "currency": currency,
                     "required_lead_time_weeks": float(lead), "standard_payment_days": int(terms),
                     "destination": destination, "evaluation_date": eval_date},
                    ctx["actor"],
                )
                st.session_state["event_id"] = event_id
                st.rerun()

event_id = st.session_state.get("event_id") or ctx["event_id"]
if not event_id or not db.get_event(event_id):
    st.info("Load the demo scenario or create an event to continue.")
    st.stop()

event, rfq = ui.load_rfq(event_id)
st.subheader(event["name"])
m1, m2, m3, m4 = st.columns(4)
m1.metric("Quantity", f"{rfq.quantity:,}")
m2.metric("Required lead time", f"{rfq.required_lead_time_weeks:g} wks")
m3.metric("Standard terms", f"Net {rfq.standard_payment_days}")
m4.metric("Evaluation date", rfq.evaluation_date.isoformat())
st.caption(f"Item: {rfq.item} · Ship-to: {rfq.destination}")

uploads = st.file_uploader("Upload supplier quotes", type=list(SUPPORTED_TYPES), accept_multiple_files=True)
if ctx["mode"] == "demo":
    st.caption("Demo mode only processes the bundled sample quotes. Other files need Live mode and an API key.")
if uploads and st.button(f"Extract {len(uploads)} document(s)", type="primary"):
    run_documents(event_id, [(u.name, u.getvalue()) for u in uploads])
    st.rerun()

for r in st.session_state.pop("last_results", []):
    if r["status"] == "failed":
        st.error(f"{r['filename']}: {r['error']}. The quote can be entered manually on the Review page.")
    else:
        src = {"fixture": "recorded (hand-labeled)", "claude": "recorded Claude run", "live": "live Claude call"}
        st.success(ui.md(f"{r['filename']}: extracted ({src.get(r['source'], r['source'])}, ${r['cost_usd']:.4f})"))

quotes = db.list_quotes(event_id)
if quotes:
    st.markdown("#### Quotes in this event")
    table = pd.DataFrame([
        {
            "File": q["filename"],
            "Supplier": ((q["reviewed"] or {}).get("supplier_name") or {}).get("value", "-"),
            "Status": ui.STATUS_BADGE.get(q["status"], q["status"]),
            "High flags": sum(f["severity"] == "high" for f in q["flags"]),
            "Medium flags": sum(f["severity"] == "medium" for f in q["flags"]),
        }
        for q in quotes
    ])
    st.dataframe(table, hide_index=True, width="stretch")
    ui.nav_link("pages/2_Review_Approve.py", "Next: review and approve", "➡️")

with st.expander("Danger zone"):
    if st.button("Delete this bid event"):
        db.delete_event(event_id, ctx["actor"])
        st.session_state.pop("event_id", None)
        st.rerun()
