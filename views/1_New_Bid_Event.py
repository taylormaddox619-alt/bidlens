import json
import random
from datetime import date

import pandas as pd
import streamlit as st

from reload_guard import ensure_fresh

ensure_fresh()  # load current bidlens code after a redeploy (see reload_guard.py)

from bidlens import config, db, ui, workflow
from bidlens.config import SAMPLES_DIR
from bidlens.ingest import SUPPORTED_TYPES

ui.setup_page("New Bid Event")
ctx = ui.sidebar()

ui.page_header("New Bid Event", ui.PAGE_ICON["views/1_New_Bid_Event.py"],
               "Create an RFQ, then upload the supplier quotes you received.")


def generate_random_rfq() -> dict:
    from bidlens.generate import random_rfq
    return random_rfq(random.Random(), date.today())


def sample_files() -> list[tuple[str, bytes]]:
    return [(p.name, p.read_bytes()) for p in sorted(SAMPLES_DIR.iterdir()) if p.suffix in (".pdf", ".xlsx")]


def run_documents(event_id: str, files: list[tuple[str, bytes]]) -> None:
    _, rfq = ui.load_rfq(event_id)
    with st.spinner(f"Extracting {len(files)} document(s)…"):
        results = workflow.process_documents(event_id, rfq, files, ctx["mode"], ctx["api_key"], ctx["actor"])
        workflow.refresh_flags(event_id, rfq)
    st.session_state["last_results"] = results


# --- AI-generated test quotes ------------------------------------------------------------------
privileged = ctx["mode"] == "live"  # passcode holders are not subject to the public daily cap
used_today = db.public_generations_today()
remaining = max(config.PUBLIC_DAILY_GENERATIONS - used_today, 0)
can_generate = bool(ctx["api_key"]) and (privileged or remaining > 0)


def generation_note() -> str:
    if not ctx["api_key"]:
        return "Unavailable: no Anthropic API key configured for this deployment."
    if privileged:
        return "Live mode: not counted against the public daily limit."
    if remaining == 0:
        return f"The public demo's daily limit ({config.PUBLIC_DAILY_GENERATIONS}) is used up. Try again tomorrow (UTC)."
    return f"{remaining} of {config.PUBLIC_DAILY_GENERATIONS} public generations left today · about 1 minute · uses Claude."


def run_generation(event_id: str) -> None:
    _, rfq = ui.load_rfq(event_id)
    with st.status("Generating fresh supplier quotes…", expanded=True) as status:
        st.write(f":material/casino: Code is randomly choosing {config.QUOTES_PER_GENERATION} suppliers' prices, countries, "
                 "currencies, terms and problems (kept as a hidden answer key).")
        st.write(":material/edit_note: Claude is writing each supplier's quote document, then a separate call reads them blind…")
        outcome = workflow.generate_test_quotes(event_id, rfq, ctx["api_key"], ctx["actor"], privileged)
        status.update(label=f"Generated and extracted {len(outcome['results'])} quotes "
                            f"(${outcome['cost_usd']:.2f})", state="complete", expanded=False)
    st.session_state["last_results"] = outcome["results"]
    st.session_state["generation_scores"] = outcome["scores"]


with st.expander("Quick start", expanded=not db.list_events(), icon=":material/bolt:"):
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("**Guided demo**")
        st.markdown(
            "Creates **RFQ-2026-0142**: 500 cast-iron compressor air-end housings, 12-week lead time, Net 60 "
            "standard terms, with **4 fictional supplier quotes** (3 PDFs, 1 Excel). Each has a built-in problem to catch."
        )
    with right:
        st.markdown("**Fresh AI-generated scenario**")
        st.markdown(
            "A new random RFQ with suppliers nobody has seen before. Code picks their terms at random and keeps them "
            "as a **hidden answer key**. Claude writes the quote documents, then the normal pipeline reads them blind "
            "and is **scored against the answer key**, misses included."
        )
    # The buttons get a row of their own so they line up whatever the length of the text above them.
    left, right = st.columns(2, gap="large")
    with left:
        if st.button("Load demo scenario", type="primary", icon=":material/play_arrow:"):
            rfq = json.loads((SAMPLES_DIR / "demo_rfq.json").read_text(encoding="utf-8"))
            event_id = db.create_event(rfq, ctx["actor"])
            st.session_state["event_id"] = event_id
            run_documents(event_id, sample_files())
            st.rerun()
    with right:
        if st.button("Generate a fresh scenario", disabled=not can_generate, icon=":material/casino:"):
            new_rfq = generate_random_rfq()
            event_id = db.create_event(new_rfq, ctx["actor"])
            st.session_state["event_id"] = event_id
            run_generation(event_id)
            st.rerun()
        st.caption(generation_note())

with st.expander("Create a custom bid event", icon=":material/add_circle:"):
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
        if st.form_submit_button("Create event", type="primary"):
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
                st.session_state["flash"] = f"Created **{name}**. It's now the active bid event (see the sidebar)."
                st.rerun()

event_id = st.session_state.get("event_id") or ctx["event_id"]
if not event_id or not db.get_event(event_id):
    st.info("Load the demo scenario or create an event to continue.")
    st.stop()

if "flash" in st.session_state:
    st.success(st.session_state.pop("flash"))

event, rfq = ui.load_rfq(event_id)
quotes = db.list_quotes(event_id)
pending = [q for q in quotes if q["status"] not in ui.REVIEWED]

st.divider()
st.subheader(event["name"], anchor=False)
st.caption(f"Item: {rfq.item} · Ship-to: {rfq.destination}")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Quantity", f"{rfq.quantity:,}")
m2.metric("Required lead time", f"{rfq.required_lead_time_weeks:g} wks")
m3.metric("Standard terms", f"Net {rfq.standard_payment_days}")
m4.metric("Evaluation date", rfq.evaluation_date.isoformat())

_, award = ui.workflow_stepper(event_id, "setup", quotes)

if not quotes:
    st.info("**Your next step: add the supplier quotes you received for this RFQ.** Upload PDF or Excel quote "
            "files at the bottom of this page, or use one of the options below.")
    if ctx["mode"] == "demo":
        st.warning("You're in **Demo mode**, which can only read the 4 bundled sample quotes. To analyze your own "
                   "quote files, switch to **Live** in the sidebar (passcode required).")
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("**No quote files handy? Generate fresh ones**")
        if st.button("Generate supplier quotes for this RFQ with AI", disabled=not can_generate,
                     icon=":material/casino:"):
            run_generation(event_id)
            st.rerun()
        st.caption("Suppliers quote *this* event's item and quantity, with random terms scored against a hidden "
                   "answer key. " + generation_note())
    with right:
        st.markdown("**Or use the bundled samples**")
        if st.button("Try this event with the 4 sample supplier quotes"):
            run_documents(event_id, sample_files())
            st.rerun()
        st.caption("The samples quote a compressor housing, but the rules check them against *this* event's "
                   "quantity, lead time and payment terms.")

scores = st.session_state.pop("generation_scores", None)
if scores:
    graded = [s for s in scores if s["total"]]
    correct, total = sum(s["correct"] for s in graded), sum(s["total"] for s in graded)
    with st.container(border=True):
        st.markdown(f"#### :material/casino: Answer-key check: {correct} of {total} fields extracted correctly "
                    f"({correct / total:.0%})" if total else "#### :material/casino: Answer-key check")
        st.caption("Each quote was written by Claude from terms that code chose at random. The extraction never saw "
                   "those terms; this compares what it read with the hidden answer key.")
        st.dataframe(pd.DataFrame([{
            "Document": s["filename"], "Style": s["style"],
            "Fields correct": f"{s['correct']}/{s['total']}" if s["total"] else s.get("error") or s["status"],
            "Misses": ", ".join(s["misses"]) or "none",
            "Not graded (writer left out)": ", ".join(s["omitted_by_writer"]) or "",
        } for s in scores]), hide_index=True, width="stretch")
        st.caption("Open any quote on **Review & Approve** to see its full answer key next to the extraction.")

extracted_lines = []
for r in st.session_state.pop("last_results", []):
    if r["status"] == "failed":
        st.error(f"{r['filename']}: {r['error']} The quote can be entered manually on the Review page.")
    elif r["status"] == "duplicate":
        st.warning(f"{r['filename']}: skipped. {r['error']}")
    else:
        src = {"fixture": "recorded (hand-labeled)", "claude": "recorded Claude run", "live": "live Claude call"}
        route = ""
        if r["source"] != "fixture" and r.get("model"):
            route = f", escalated to {r['model']}" if r["escalated"] else f", {r['model']}"
        extracted_lines.append(f"- `{r['filename']}`: {src.get(r['source'], r['source'])}{route}, "
                               f"${r['cost_usd']:.4f}")
if extracted_lines:  # one summary box rather than a banner per document
    st.success(ui.md(f"**Extracted {len(extracted_lines)} document{'s' if len(extracted_lines) != 1 else ''}**\n"
                     + "\n".join(extracted_lines)), icon=":material/task_alt:")

if quotes:
    st.markdown("#### Quotes in this event")
    high_pending = [q for q in pending if ui.risk_level(q["flags"]) == "high"]
    if high_pending:
        st.error(f"**{len(high_pending)} quote{'s have' if len(high_pending) != 1 else ' has'} high-risk issues.** "
                 f"Review {'those' if len(high_pending) != 1 else 'it'} first: "
                 + ", ".join(ui.short_name(ui.quote_supplier(q)) for q in high_pending), icon=":material/error:")
    ordered = sorted(quotes, key=lambda q: (q["status"] in ui.REVIEWED, ui.RISK_RANK[ui.risk_level(q["flags"])]))
    table = pd.DataFrame([
        {
            "Risk": ui.RISK_LABEL[ui.risk_level(q["flags"])],
            "Supplier": ui.quote_supplier(q),
            "Status": ui.STATUS_BADGE.get(q["status"], q["status"]),
            "🔴 High": ui.flag_counts(q["flags"])["high"],
            "🟠 Medium": ui.flag_counts(q["flags"])["medium"],
            "File": q["filename"],
        }
        for q in ordered
    ])
    st.dataframe(table, hide_index=True, width="stretch")
    st.caption(ui.severity_legend())
    ui.next_step_button(quotes, award, "setup", key="next_bottom_setup")

st.markdown("#### Add quotes")
# The key changes after each extraction, which empties the uploader so a second click cannot resubmit the files.
uploads = st.file_uploader("Upload supplier quotes (PDF, Excel, text)", type=list(SUPPORTED_TYPES),
                           accept_multiple_files=True, key=f"uploads_{st.session_state.get('upload_round', 0)}")
if uploads and st.button(f"Extract {len(uploads)} document(s)", type="primary"):
    run_documents(event_id, [(u.name, u.getvalue()) for u in uploads])
    st.session_state["upload_round"] = st.session_state.get("upload_round", 0) + 1
    st.rerun()

with st.expander("Danger zone"):
    if st.button("Delete this bid event"):
        db.delete_event(event_id, ctx["actor"])
        st.session_state.pop("event_id", None)
        st.rerun()
