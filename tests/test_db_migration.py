"""Existing database files gain the cache/effort columns on connect, and new runs log them."""

import duckdb

from bidlens import db

OLD_LLM_RUNS = """
CREATE TABLE llm_runs (
    id VARCHAR PRIMARY KEY, ts TIMESTAMP, event_id VARCHAR, quote_id VARCHAR, purpose VARCHAR,
    source VARCHAR, model VARCHAR, prompt_version VARCHAR, input_tokens INTEGER, output_tokens INTEGER,
    cost_usd DOUBLE, latency_s DOUBLE, status VARCHAR, error VARCHAR, request_id VARCHAR
);
"""


def test_old_database_is_migrated_and_new_runs_log_cache_fields(tmp_path, monkeypatch):
    path = tmp_path / "old.duckdb"
    conn = duckdb.connect(str(path))
    conn.execute(OLD_LLM_RUNS)
    conn.execute("INSERT INTO llm_runs VALUES ('r1', now(), 'e', 'q', 'extract', 'live', 'm', 'v', 10, 5, "
                 "0.001, 1.0, 'ok', NULL, 'req')")
    conn.close()

    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(db, "_conn", None)
    cols = {c[0] for c in db.cursor().execute("SELECT * FROM llm_runs").description}
    assert {"cache_read_tokens", "cache_write_tokens", "effort"} <= cols

    db.log_llm_run({"source": "live", "model": "m", "input_tokens": 100, "output_tokens": 50, "cost_usd": 0.01,
                    "latency_s": 2.0, "status": "ok", "cache_read_tokens": 700, "cache_write_tokens": 0,
                    "effort": "low"}, "extract")
    rows = db._rows("SELECT id, cache_read_tokens, effort FROM llm_runs ORDER BY ts")
    assert rows[0]["id"] == "r1" and rows[0]["cache_read_tokens"] is None  # pre-migration row survives
    assert rows[1]["cache_read_tokens"] == 700 and rows[1]["effort"] == "low"
    db._conn.close()
    monkeypatch.setattr(db, "_conn", None)
