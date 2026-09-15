import html
import re

import pandas as pd
import streamlit as st

from reload_guard import ensure_fresh

ensure_fresh()  # load current bidlens code after a redeploy (see reload_guard.py)

from bidlens import db, review, ui, workflow
from bidlens.grading import compare_fields
from bidlens.ingest import quote_in_document
from bidlens.rules import SEVERITY_ORDER, label
from bidlens.schemas import FIELD_SPECS

ui.setup_page("Review & Approve", "🔎")
ctx = ui.sidebar()

st.title("🔎 Review & Approve")
st.caption("Human-in-the-loop checkpoint: nothing reaches the comparison until a buyer approves it.")

event_id = ctx["event_id"]
if not event_id:
    st.info("Create or load a bid event first.")
    ui.nav_link("pages/1_New_Bid_Event.py", "Go to New Bid Event", "🆕")
    st.stop()

event, rfq = ui.load_rfq(event_id)
quotes = db.list_quotes(event_id)
if not quotes:
    st.info("**No quotes in this event yet.** 👉 Add supplier quotes on **New Bid Event** first.")
    ui.nav_link("pages/1_New_Bid_Event.py", "Go to New Bid Event", "🆕")
    st.stop()

ui.workflow_stepper(event_id, "review")

if "review_flash" in st.session_state:
    st.success(st.session_state.pop("review_flash"))

done = sum(q["status"] in ui.REVIEWED for q in quotes)
if done == len(quotes):
    award = db.get_award(event_id)
    st.success("**✅ All quotes are reviewed"
               + (f" and the award is recorded ({ui.short_name(award['supplier'])}).** " if award
                  else ".** Use the **Next** button above to compare bids and record the award. ")
               + "You can still reopen any quote below.")

with st.expander("📋 How to review a quote", expanded=done == 0):
    st.markdown(
        "1. **Read the colored boxes.** 🔴 Red = high risk, 🟠 amber = medium risk. Each item says what to do.\n"
        "2. **Check the evidence.** The document on the right highlights the text each value came from.\n"
        "3. **Fix wrong values** in the *Value* column, then click **Save edits**. The rules re-run automatically.\n"
        "4. **Approve** the quote to include it in the comparison (tick the box to accept any remaining red flags), "
        "or **Reject** it. The next quote opens automatically, highest risk first."
    )
    st.caption(ui.severity_legend())


def quote_label(q: dict) -> str:
    return f"{ui.risk_badge(q['flags'], compact=True)} · {ui.STATUS_BADGE.get(q['status'], q['status'])} · " \
           f"{ui.quote_supplier(q)}"


# Pending quotes first, highest risk first.
ordered = sorted(quotes, key=lambda q: (q["status"] in ui.REVIEWED, ui.RISK_RANK[ui.risk_level(q["flags"])]))
ids = [q["id"] for q in ordered]
by_id = {q["id"]: q for q in quotes}
selected = st.session_state.get("review_quote")
quote_id = st.selectbox("Quote to review (pending and highest risk first)", ids,
                        index=ids.index(selected) if selected in ids else 0,
                        format_func=lambda i: quote_label(by_id[i]))
st.session_state["review_quote"] = quote_id
q = by_id[quote_id]


def after_decision(verb: str) -> None:
    """Remember what just happened and what's next, then let the page open the next pending quote."""
    remaining = [x for x in ordered if x["status"] not in ui.REVIEWED and x["id"] != q["id"]]
    name = ui.short_name(ui.quote_supplier(q))
    if remaining:
        upcoming = remaining[0]
        st.session_state["review_flash"] = (
            f"{verb} **{name}**. Next up: **{ui.short_name(ui.quote_supplier(upcoming))}** "
            f"({ui.risk_badge(upcoming['flags'])}) · {len(remaining)} left.")
    else:
        st.session_state["review_flash"] = f"{verb} **{name}**. That was the last quote."
    st.session_state.pop("review_quote", None)


def empty_quote() -> dict:
    blank = {name: {"value": None, "source_quote": None, "confidence": "high"} for name, *_ in FIELD_SPECS}
    return {**blank, "price_tiers": [], "supplier_exceptions": []}


if q["reviewed"] is None:
    st.warning("Automatic extraction failed for this document. Enter the terms manually, or reject the quote.")
    if st.button("Enter manually"):
        db.update_review(q["id"], empty_quote(), [], [], ctx["actor"], event_id)
        st.rerun()
    if st.button("Reject quote"):
        db.set_quote_status(q["id"], "rejected", ctx["actor"], event_id, "Unreadable document")
        st.rerun()
    st.stop()

reviewed = q["reviewed"]
locked = q["status"] in ui.REVIEWED

# --- Risks ---------------------------------------------------------------------------
flags = q["flags"]
highs = [f for f in flags if f["severity"] == "high"]
st.markdown(f"#### Risks for {ui.short_name(ui.quote_supplier(q))} · {ui.risk_badge(flags)}")
ui.render_flags(flags)

answer_key = db.get_answer_key(quote_id)
if answer_key and q["extraction"]:
    fields = compare_fields(answer_key["truth"], q["extraction"], skip={"payment_terms", *answer_key["omitted"]})
    correct = sum(f["correct"] for f in fields)
    with st.expander(f"🎲 AI-generated test quote · answer key check: {correct}/{len(fields)} fields correct"):
        st.caption("Code chose these terms at random, and Claude wrote the document from them. The extraction "
                   "below never saw this answer key. Comparison uses the AI's original extraction, before any "
                   f"buyer edits. Style: {answer_key['style']}.")
        st.dataframe(pd.DataFrame([{
            "": "✅" if f["correct"] else "❌", "Field": f["label"],
            "AI extracted": review.display_value(f["actual"]) if f["field"] != "price_tiers" else str(f["actual"]),
            "Answer key": review.display_value(f["expected"]) if f["field"] != "price_tiers" else str(f["expected"]),
        } for f in fields]), hide_index=True, width="stretch")
        if answer_key["omitted"]:
            st.caption("Not graded because the writer left them out of the document: " + ", ".join(answer_key["omitted"]))

left, right = st.columns([3, 2], gap="large")

# --- Editable extraction ------------------------------------------------------------
with left:
    st.markdown("#### Extracted terms")
    field_icon = {}  # most severe flag per field
    for f in sorted(flags, key=lambda f: SEVERITY_ORDER[f["severity"]]):
        field_icon.setdefault(f["field"], ui.SEVERITY_ICON[f["severity"]])
    rows = []
    for name, lbl, kind, required in FIELD_SPECS:
        field = reviewed.get(name) or {}
        value = field.get("value")
        src = field.get("source_quote")
        if field.get("edited"):
            evidence = "✏️ buyer entered"
        elif value is None:
            evidence = "not in document"
        elif src and quote_in_document(src, q["doc_text"]):
            evidence = "✓ cited"
        else:
            evidence = "⚠ citation not found"
        rows.append({
            "field": name,
            "Field": (f"{field_icon[name]} " if name in field_icon else "") + lbl + (" *" if required else ""),
            "Value": review.display_value(value),
            "Confidence": field.get("confidence", ""),
            "Evidence": evidence,
            "Source text": src or "",
        })
    df = pd.DataFrame(rows)
    edited_df = st.data_editor(
        df, key=f"fields_{quote_id}", hide_index=True, width="stretch", disabled=locked,
        height=36 * (len(rows) + 1) + 3,
        column_config={
            "field": None,
            "Field": st.column_config.TextColumn(disabled=True, width=170),
            "Value": st.column_config.TextColumn(help="Edit to correct the extraction", width=190),
            "Confidence": st.column_config.TextColumn(disabled=True, width=85),
            "Evidence": st.column_config.TextColumn(disabled=True, width=130),
            "Source text": st.column_config.TextColumn(disabled=True, width="large"),
        },
    )
    st.caption("🔴/🟠 next to a field = it has a flag in the boxes above · * = required · "
               "double-click a Value to edit it")

    st.markdown("**Price breaks**")
    tiers_df = pd.DataFrame(reviewed.get("price_tiers") or [],
                            columns=["min_qty", "max_qty", "unit_price", "source_quote"])
    edited_tiers = st.data_editor(
        tiers_df, key=f"tiers_{quote_id}", hide_index=True, width="stretch", num_rows="dynamic",
        disabled=locked,
        column_config={"min_qty": st.column_config.NumberColumn("Min qty", step=1),
                       "max_qty": st.column_config.NumberColumn("Max qty (blank = open)", step=1),
                       "unit_price": st.column_config.NumberColumn("Unit price", format="%.2f"),
                       "source_quote": st.column_config.TextColumn("Source text", disabled=True)},
    )
    exceptions_text = st.text_area("Supplier exceptions / assumptions (one per line)",
                                   "\n".join(reviewed.get("supplier_exceptions") or []),
                                   key=f"exc_{quote_id}", disabled=locked)


def apply_edits() -> tuple[dict, list[tuple], list[str]]:
    return review.apply_edits(reviewed, edited_df.to_dict("records"), edited_tiers.to_dict("records"),
                              exceptions_text)


with left:
    if locked:
        st.info(f"This quote is **{q['status']}** by {q['reviewed_by']}"
                + (f": {q['review_note']}" if q["review_note"] else "."))
        if st.button("Reopen for review"):
            db.set_quote_status(q["id"], "extracted", ctx["actor"], event_id, "Reopened")
            st.rerun()
    else:
        c1, c2 = st.columns(2)
        if c1.button("💾 Save edits", width="stretch"):
            new, edits, errors = apply_edits()
            if errors:
                for e in errors:
                    st.error(e)
            else:
                db.update_review(q["id"], new, q["flags"], edits, ctx["actor"], event_id)
                workflow.refresh_flags(event_id, rfq)
                st.toast(f"Saved {len(edits)} change(s); rules re-run.")
                st.rerun()

        st.markdown("#### Decision")
        new, edits, errors = apply_edits()
        if edits:
            st.warning("✏️ You have unsaved edits. Click **Save edits** before approving.")
        missing_core = [label(n) for n in ("unit_price", "currency") if (reviewed.get(n) or {}).get("value") is None]
        ack = True
        if highs:
            with st.container(border=True):
                st.markdown(ui.md(f"**🔴 To approve, you must accept {len(highs)} high-risk issue"
                                  f"{'s' if len(highs) != 1 else ''}:**\n"
                                  + "\n".join(f"- {label(f['field'])}: {f['message']}" for f in highs)))
                ack = st.checkbox("I have checked these high-risk issues and accept this quote for comparison, "
                                  "with the issues recorded.", key=f"ack_{quote_id}")
        note = st.text_input("Review note (optional; recorded in the audit log)", key=f"note_{quote_id}")
        a, r = st.columns(2)
        can_approve = ack and not edits and not missing_core
        if a.button("✅ Approve quote", type="primary", disabled=not can_approve, width="stretch"):
            db.set_quote_status(q["id"], "approved", ctx["actor"], event_id, note)
            after_decision("✅ Approved")
            st.rerun()
        if r.button("⛔ Reject quote", width="stretch"):
            db.set_quote_status(q["id"], "rejected", ctx["actor"], event_id, note or "Rejected by buyer")
            workflow.refresh_flags(event_id, rfq)
            after_decision("⛔ Rejected")
            st.rerun()
        if missing_core:
            st.caption(f"Approval blocked: {', '.join(missing_core)} required for landed-cost comparison.")
        elif highs and not ack:
            st.caption("Approve is disabled until you tick the box above.")


# --- Source document with highlighted citations ----------------------------------
def highlighted(text: str, quotes_to_mark: list[str]) -> str:
    escaped = html.escape(text)
    for src in sorted(set(quotes_to_mark), key=len, reverse=True):
        words = [re.escape(html.escape(w)) for w in src.split()]
        if not words:
            continue
        pattern = re.compile(r"\s+".join(words), re.IGNORECASE)
        escaped = pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", escaped, count=1)
    return escaped


with right:
    st.markdown(f"#### Source document · `{q['filename']}`")
    cites = [(reviewed.get(n) or {}).get("source_quote") for n, *_ in FIELD_SPECS]
    cites += [t.get("source_quote") for t in reviewed.get("price_tiers") or []]
    body = highlighted(q["doc_text"], [c for c in cites if c])
    st.markdown(
        "<div style='max-height:640px;overflow:auto;padding:12px;border:1px solid rgba(128,128,128,.35);"
        "border-radius:8px;font-family:ui-monospace,Consolas,monospace;font-size:12.5px;white-space:pre-wrap;"
        f"line-height:1.5'>{body}</div>",
        unsafe_allow_html=True,
    )
    st.caption("Highlighted text = evidence cited by the extraction.")

st.divider()
ui.next_step_button(event_id, "review", key="next_bottom_review")
