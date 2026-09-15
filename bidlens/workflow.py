"""Workflow orchestration shared by the UI, scripts, and evals."""

from . import costing, db, rules
from .extract import extract
from .ingest import extract_text, text_sha
from .schemas import RFQ, flat_values
from .scoring import score_bids


def process_document(event_id: str, rfq: RFQ, filename: str, data: bytes, mode: str,
                     api_key: str | None, actor: str) -> dict:
    """Ingest -> extract -> store. Returns a summary for the UI."""
    try:
        text = extract_text(filename, data)
    except Exception as e:  # corrupt or unsupported file
        quote_id = db.add_quote(event_id, filename, None, "", None, [], "failed", actor)
        return {"filename": filename, "quote_id": quote_id, "status": "failed", "error": f"Could not read file: {e}"}

    quote, meta = extract(text, rfq, mode, api_key)
    status = "extracted" if quote else "failed"
    quote_id = db.add_quote(event_id, filename, text_sha(text), text, quote, [], status, actor)
    if meta.get("source") in ("live", "demo_cache"):
        db.log_llm_run(meta, "extract", event_id, quote_id)
    return {"filename": filename, "quote_id": quote_id, "status": status, "error": meta.get("error"),
            "source": meta.get("recorded_source") or meta.get("source"), "cost_usd": meta.get("cost_usd", 0.0)}


def refresh_flags(event_id: str, rfq: RFQ) -> None:
    """Re-run rules for every quote in the event (peer price comparison needs all of them)."""
    quotes = [q for q in db.list_quotes(event_id) if q["reviewed"] and q["status"] != "rejected"]
    peers = [rules.unit_price_usd(flat_values(q["reviewed"])) for q in quotes]
    for q in quotes:
        flags = [f.to_dict() for f in rules.evaluate(q["reviewed"], rfq, q["doc_text"], peers)]
        db.update_review(q["id"], q["reviewed"], flags, [], "system", event_id)


def comparison(event_id: str, rfq: RFQ, weights: dict) -> list[dict]:
    """Ranked rows for approved quotes, best first."""
    approved = [q for q in db.list_quotes(event_id) if q["status"] == "approved"]
    bids = []
    for q in approved:
        values = flat_values(q["reviewed"])
        bids.append({"quote_id": q["id"], "values": values, "flags": q["flags"],
                     "landed": costing.landed_cost(values, rfq)})
    if not bids:
        return []
    by_id = {b["quote_id"]: b for b in bids}
    rows = []
    for s in score_bids(bids, rfq, weights):
        b = by_id[s.quote_id]
        rows.append({**b, "supplier": s.supplier, "overall": s.overall, "cost_score": s.cost_score,
                     "lead_time_score": s.lead_time_score, "terms_score": s.terms_score,
                     "risk_score": s.risk_score, "rationale": s.rationale})
    return rows
