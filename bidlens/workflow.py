"""Workflow orchestration shared by the UI, scripts, and evals."""

from concurrent.futures import ThreadPoolExecutor

from . import config, costing, db, rules
from .extract import extract
from .grading import compare_fields
from .ingest import extract_text, text_sha
from .schemas import RFQ, flat_values
from .scoring import score_bids


def _read_and_extract(filename: str, data: bytes, rfq: RFQ, mode: str, api_key: str | None) -> dict:
    """Pure step (no database access), safe to run in worker threads."""
    try:
        text = extract_text(filename, data)
    except Exception as e:  # corrupt or unsupported file
        return {"filename": filename, "text": "", "quote": None, "meta": {"error": f"Could not read file: {e}"}}
    quote, meta = extract(text, rfq, mode, api_key)
    return {"filename": filename, "text": text, "quote": quote, "meta": meta}


def _store(event_id: str, result: dict, actor: str) -> dict:
    text, quote, meta = result["text"], result["quote"], result["meta"]
    status = "extracted" if quote else "failed"
    quote_id = db.add_quote(event_id, result["filename"], text_sha(text) if text else None, text, quote, [],
                            status, actor)
    if meta.get("source") in ("live", "demo_cache"):
        # Log each model call separately so the scorecard can show spend by model and escalation rate.
        for run in meta.get("runs") or [meta]:
            db.log_llm_run({**run, "source": meta["source"]}, run.get("purpose", "extract"), event_id, quote_id)
    return {"filename": result["filename"], "quote_id": quote_id, "status": status, "error": meta.get("error"),
            "source": meta.get("recorded_source") or meta.get("source"), "cost_usd": meta.get("cost_usd", 0.0),
            "model": meta.get("model"), "escalated": meta.get("escalated", False)}


def process_document(event_id: str, rfq: RFQ, filename: str, data: bytes, mode: str,
                     api_key: str | None, actor: str) -> dict:
    """Ingest -> extract -> store. Returns a summary for the UI."""
    return _store(event_id, _read_and_extract(filename, data, rfq, mode, api_key), actor)


def process_documents(event_id: str, rfq: RFQ, files: list[tuple[str, bytes]], mode: str,
                      api_key: str | None, actor: str) -> list[dict]:
    """Extract several documents in parallel (live calls are I/O-bound), then store them in order."""
    with ThreadPoolExecutor(max_workers=max(1, min(len(files), 4))) as pool:
        results = list(pool.map(lambda f: _read_and_extract(f[0], f[1], rfq, mode, api_key), files))
    return [_store(event_id, r, actor) for r in results]


def generate_test_quotes(event_id: str, rfq: RFQ, api_key: str, actor: str, privileged: bool,
                         seed: int | None = None) -> dict:
    """Have Claude write fresh supplier quotes from code-chosen terms, extract them blind, and score them.

    Returns {"results": [...], "scores": [...], "cost_usd": float}.
    """
    from .generate import build_test_quotes  # imported lazily: reportlab is only needed here

    rfq_dict = rfq.model_dump()
    suppliers = build_test_quotes(api_key, rfq_dict, config.QUOTES_PER_GENERATION, seed)
    written = [s for s in suppliers if s.document]
    results = process_documents(event_id, rfq, [(s.filename, s.document) for s in written], "live", api_key, actor)

    scores, cost = [], sum(s.writer_meta.get("cost_usd", 0.0) for s in suppliers)
    for supplier, result in zip(written, results):
        db.log_llm_run(supplier.writer_meta, "generate", event_id, result["quote_id"])
        db.save_answer_key(result["quote_id"], event_id, supplier.truth, supplier.omitted_fields, supplier.style)
        cost += result["cost_usd"]
        extracted = next((q["extraction"] for q in db.list_quotes(event_id) if q["id"] == result["quote_id"]), None)
        fields = compare_fields(supplier.truth, extracted, skip={"payment_terms", *supplier.omitted_fields}) \
            if extracted else []
        scores.append({"filename": supplier.filename, "style": supplier.style,
                       "correct": sum(f["correct"] for f in fields), "total": len(fields),
                       "misses": [f["label"] for f in fields if not f["correct"]],
                       "omitted_by_writer": supplier.omitted_fields, "status": result["status"]})
    for supplier in suppliers:
        if not supplier.document:
            db.log_llm_run(supplier.writer_meta, "generate", event_id)
            scores.append({"filename": supplier.filename, "style": supplier.style, "correct": 0, "total": 0,
                           "misses": [], "omitted_by_writer": [], "status": "failed",
                           "error": supplier.writer_meta.get("error")})

    refresh_flags(event_id, rfq)
    db.log_generation(event_id, actor, privileged, len(written), cost)
    return {"results": results, "scores": scores, "cost_usd": cost}


def refresh_flags(event_id: str, rfq: RFQ) -> None:
    """Re-run rules for every quote in the event (peer price comparison needs all of them)."""
    quotes = [q for q in db.list_quotes(event_id) if q["reviewed"] and q["status"] != "rejected"]
    peers = [rules.unit_price_usd(flat_values(q["reviewed"])) for q in quotes]
    for q in quotes:
        flags = [f.to_dict() for f in rules.evaluate(q["reviewed"], rfq, q["doc_text"], peers)]
        db.update_review(q["id"], q["reviewed"], flags, [], "system", event_id)


def _landed(quote: dict, rfq: RFQ) -> tuple[costing.LandedCost | None, str | None]:
    """(landed cost, None), or (None, reason) when the quote cannot be costed. Never raises.

    The review page blocks approval of an uncostable quote, but rows approved before that check existed
    are still in deployed databases, so the comparison has to tolerate them.
    """
    values = flat_values(quote["reviewed"] or {})
    reason = costing.uncostable(values, rfq.quantity)
    if reason:
        return None, reason
    try:
        return costing.landed_cost(values, rfq), None
    except ValueError as e:
        return None, str(e)


def excluded_from_comparison(event_id: str, rfq: RFQ) -> list[dict]:
    """Approved quotes that `comparison` leaves out, each with the reason, so the page can say so."""
    excluded = []
    for q in db.list_quotes(event_id):
        if q["status"] != "approved":
            continue
        _, reason = _landed(q, rfq)
        if reason:
            name = ((q["reviewed"] or {}).get("supplier_name") or {}).get("value")
            excluded.append({"quote_id": q["id"], "supplier": name or q["filename"], "filename": q["filename"],
                             "reason": reason})
    return excluded


def comparison(event_id: str, rfq: RFQ, weights: dict) -> list[dict]:
    """Ranked rows for approved quotes that can be costed, best first (see `excluded_from_comparison`)."""
    approved = [q for q in db.list_quotes(event_id) if q["status"] == "approved"]
    bids = []
    for q in approved:
        landed, reason = _landed(q, rfq)
        if reason:
            continue
        bids.append({"quote_id": q["id"], "filename": q["filename"], "values": flat_values(q["reviewed"]),
                     "flags": q["flags"], "landed": landed})
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
