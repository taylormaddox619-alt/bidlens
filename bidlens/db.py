"""Persistence layer (DuckDB).

All SQL lives here so the store can be swapped for Snowflake (same ANSI SQL,
`snowflake-connector-python` cursor API) without touching the app.
"""

import json
import threading
import uuid
from datetime import date, datetime, timezone

import duckdb

from .config import DB_PATH

_lock = threading.Lock()
_conn: duckdb.DuckDBPyConnection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS bid_events (
    id VARCHAR PRIMARY KEY, name VARCHAR, item VARCHAR, quantity INTEGER, currency VARCHAR,
    required_lead_time_weeks DOUBLE, standard_payment_days INTEGER, destination VARCHAR,
    evaluation_date DATE, created_by VARCHAR, created_at TIMESTAMP, status VARCHAR
);
CREATE TABLE IF NOT EXISTS quotes (
    id VARCHAR PRIMARY KEY, event_id VARCHAR, filename VARCHAR, doc_sha VARCHAR, doc_text VARCHAR,
    extraction_json VARCHAR, reviewed_json VARCHAR, flags_json VARCHAR, status VARCHAR,
    created_at TIMESTAMP, reviewed_at TIMESTAMP, reviewed_by VARCHAR, review_note VARCHAR
);
CREATE TABLE IF NOT EXISTS field_edits (
    id VARCHAR PRIMARY KEY, quote_id VARCHAR, field VARCHAR, old_value VARCHAR, new_value VARCHAR,
    edited_by VARCHAR, edited_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS llm_runs (
    id VARCHAR PRIMARY KEY, ts TIMESTAMP, event_id VARCHAR, quote_id VARCHAR, purpose VARCHAR,
    source VARCHAR, model VARCHAR, prompt_version VARCHAR, input_tokens INTEGER, output_tokens INTEGER,
    cost_usd DOUBLE, latency_s DOUBLE, status VARCHAR, error VARCHAR, request_id VARCHAR
);
CREATE TABLE IF NOT EXISTS awards (
    event_id VARCHAR PRIMARY KEY, quote_id VARCHAR, supplier VARCHAR, landed_total_usd DOUBLE,
    naive_supplier VARCHAR, naive_landed_total_usd DOUBLE, savings_vs_naive_usd DOUBLE,
    followed_recommendation BOOLEAN, justification VARCHAR, decided_by VARCHAR, decided_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS answer_keys (
    quote_id VARCHAR PRIMARY KEY, event_id VARCHAR, truth_json VARCHAR, omitted_json VARCHAR, style VARCHAR
);
CREATE TABLE IF NOT EXISTS generation_log (
    id VARCHAR PRIMARY KEY, ts TIMESTAMP, event_id VARCHAR, actor VARCHAR, privileged BOOLEAN,
    documents INTEGER, cost_usd DOUBLE
);
CREATE TABLE IF NOT EXISTS audit_log (
    id VARCHAR PRIMARY KEY, ts TIMESTAMP, event_id VARCHAR, quote_id VARCHAR, actor VARCHAR,
    action VARCHAR, detail VARCHAR
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def cursor() -> duckdb.DuckDBPyConnection:
    global _conn
    with _lock:
        if _conn is None:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            _conn = duckdb.connect(str(DB_PATH))
            _conn.execute(SCHEMA)
        return _conn.cursor()


def _rows(sql: str, params=None) -> list[dict]:
    cur = cursor()
    cur.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _json(value) -> str:
    return json.dumps(value, default=str)


# --- Audit -----------------------------------------------------------------
def audit(action: str, actor: str, event_id=None, quote_id=None, detail=None) -> None:
    cursor().execute(
        "INSERT INTO audit_log VALUES (?, ?, ?, ?, ?, ?, ?)",
        [_new_id(), _now(), event_id, quote_id, actor, action, _json(detail or {})],
    )


def audit_log(event_id: str | None = None, limit: int = 200) -> list[dict]:
    if event_id:
        return _rows("SELECT * FROM audit_log WHERE event_id = ? ORDER BY ts DESC LIMIT ?", [event_id, limit])
    return _rows("SELECT * FROM audit_log ORDER BY ts DESC LIMIT ?", [limit])


# --- Bid events --------------------------------------------------------------
def create_event(rfq: dict, actor: str) -> str:
    event_id = _new_id()
    cursor().execute(
        "INSERT INTO bid_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [event_id, rfq["event_name"], rfq["item"], rfq["quantity"], rfq["currency"],
         rfq["required_lead_time_weeks"], rfq["standard_payment_days"], rfq["destination"],
         rfq["evaluation_date"], actor, _now(), "open"],
    )
    audit("event_created", actor, event_id, detail=rfq)
    return event_id


def list_events() -> list[dict]:
    return _rows("SELECT * FROM bid_events ORDER BY created_at DESC")


def get_event(event_id: str) -> dict | None:
    rows = _rows("SELECT * FROM bid_events WHERE id = ?", [event_id])
    return rows[0] if rows else None


def event_rfq(event: dict) -> dict:
    return {
        "event_name": event["name"], "item": event["item"], "quantity": event["quantity"],
        "currency": event["currency"], "required_lead_time_weeks": event["required_lead_time_weeks"],
        "standard_payment_days": event["standard_payment_days"], "destination": event["destination"],
        "evaluation_date": event["evaluation_date"] if isinstance(event["evaluation_date"], date)
        else date.fromisoformat(str(event["evaluation_date"])),
    }


def set_event_status(event_id: str, status: str) -> None:
    cursor().execute("UPDATE bid_events SET status = ? WHERE id = ?", [status, event_id])


# --- Quotes ------------------------------------------------------------------
def add_quote(event_id, filename, doc_sha, doc_text, extraction, flags, status, actor) -> str:
    quote_id = _new_id()
    cursor().execute(
        "INSERT INTO quotes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [quote_id, event_id, filename, doc_sha, doc_text,
         _json(extraction) if extraction else None, _json(extraction) if extraction else None,
         _json(flags), status, _now(), None, None, None],
    )
    audit("quote_extracted" if extraction else "quote_extraction_failed", actor, event_id, quote_id,
          {"filename": filename, "flags": len(flags)})
    return quote_id


def list_quotes(event_id: str) -> list[dict]:
    rows = _rows("SELECT * FROM quotes WHERE event_id = ? ORDER BY created_at, filename", [event_id])
    for r in rows:
        r["extraction"] = json.loads(r["extraction_json"]) if r["extraction_json"] else None
        r["reviewed"] = json.loads(r["reviewed_json"]) if r["reviewed_json"] else None
        r["flags"] = json.loads(r["flags_json"]) if r["flags_json"] else []
    return rows


def update_review(quote_id: str, reviewed: dict, flags: list[dict], edits: list[tuple], actor: str,
                  event_id: str) -> None:
    cur = cursor()
    cur.execute("UPDATE quotes SET reviewed_json = ?, flags_json = ? WHERE id = ?",
                [_json(reviewed), _json(flags), quote_id])
    for field, old, new in edits:
        cur.execute("INSERT INTO field_edits VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [_new_id(), quote_id, field, _json(old), _json(new), actor, _now()])
    if edits:
        audit("fields_edited", actor, event_id, quote_id, {"fields": [e[0] for e in edits]})


def set_quote_status(quote_id: str, status: str, actor: str, event_id: str, note: str = "") -> None:
    cursor().execute(
        "UPDATE quotes SET status = ?, reviewed_at = ?, reviewed_by = ?, review_note = ? WHERE id = ?",
        [status, _now(), actor, note, quote_id],
    )
    audit(f"quote_{status}", actor, event_id, quote_id, {"note": note})


def delete_event(event_id: str, actor: str) -> None:
    cur = cursor()
    quote_ids = [r["id"] for r in _rows("SELECT id FROM quotes WHERE event_id = ?", [event_id])]
    for qid in quote_ids:
        cur.execute("DELETE FROM field_edits WHERE quote_id = ?", [qid])
        cur.execute("DELETE FROM answer_keys WHERE quote_id = ?", [qid])
    cur.execute("DELETE FROM quotes WHERE event_id = ?", [event_id])
    cur.execute("DELETE FROM awards WHERE event_id = ?", [event_id])
    cur.execute("DELETE FROM bid_events WHERE id = ?", [event_id])
    audit("event_deleted", actor, event_id)


# --- LLM runs ------------------------------------------------------------------
def log_llm_run(meta: dict, purpose: str, event_id=None, quote_id=None) -> None:
    cursor().execute(
        "INSERT INTO llm_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [_new_id(), _now(), event_id, quote_id, purpose, meta.get("source"), meta.get("model"),
         meta.get("prompt_version"), meta.get("input_tokens", 0), meta.get("output_tokens", 0),
         meta.get("cost_usd", 0.0), meta.get("latency_s", 0.0), meta.get("status"),
         meta.get("error"), meta.get("request_id")],
    )


# --- AI-generated test quotes ------------------------------------------------------
def save_answer_key(quote_id: str, event_id: str, truth: dict, omitted: list, style: str) -> None:
    cursor().execute("INSERT INTO answer_keys VALUES (?, ?, ?, ?, ?)",
                     [quote_id, event_id, _json(truth), _json(omitted), style])


def get_answer_key(quote_id: str) -> dict | None:
    rows = _rows("SELECT * FROM answer_keys WHERE quote_id = ?", [quote_id])
    if not rows:
        return None
    return {"truth": json.loads(rows[0]["truth_json"]), "omitted": json.loads(rows[0]["omitted_json"]),
            "style": rows[0]["style"]}


def log_generation(event_id: str, actor: str, privileged: bool, documents: int, cost: float) -> None:
    cursor().execute("INSERT INTO generation_log VALUES (?, ?, ?, ?, ?, ?, ?)",
                     [_new_id(), _now(), event_id, actor, privileged, documents, cost])
    audit("quotes_generated", actor, event_id, detail={"documents": documents, "cost_usd": cost})


def public_generations_today() -> int:
    today = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    cur = cursor()
    cur.execute("SELECT count(*) FROM generation_log WHERE ts >= ? AND NOT privileged", [today])
    return cur.fetchone()[0]


# --- Awards --------------------------------------------------------------------
def record_award(award: dict, actor: str) -> None:
    cur = cursor()
    cur.execute("DELETE FROM awards WHERE event_id = ?", [award["event_id"]])
    cur.execute(
        "INSERT INTO awards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [award["event_id"], award["quote_id"], award["supplier"], award["landed_total_usd"],
         award["naive_supplier"], award["naive_landed_total_usd"], award["savings_vs_naive_usd"],
         award["followed_recommendation"], award.get("justification", ""), actor, _now()],
    )
    set_event_status(award["event_id"], "awarded")
    audit("award_recorded", actor, award["event_id"], award["quote_id"], award)


def get_award(event_id: str) -> dict | None:
    rows = _rows("SELECT * FROM awards WHERE event_id = ?", [event_id])
    return rows[0] if rows else None


# --- Scorecard -------------------------------------------------------------------
def scorecard_data() -> dict:
    return {
        "events": _rows("SELECT * FROM bid_events"),
        "quotes": _rows("SELECT id, event_id, status, flags_json, extraction_json, created_at, reviewed_at FROM quotes"),
        "edits": _rows("SELECT * FROM field_edits"),
        "runs": _rows("SELECT * FROM llm_runs"),
        "awards": _rows("SELECT * FROM awards"),
    }
