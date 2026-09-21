"""The AI Scorecard page renders the live-run routing table, cost split and eval history without errors."""

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from bidlens import config, db

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def seeded(tmp_path, monkeypatch, rfq, samples):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.duckdb")
    monkeypatch.setattr(db, "_conn", None)
    monkeypatch.setattr(config, "EVALS_DIR", tmp_path)
    event_id = db.create_event(rfq.model_dump(), "tester")
    s = samples["jadeport"]
    quote_id = db.add_quote(event_id, s["filename"], "sha", s["text"], s["quote"], [], "extracted", "tester")
    base = {"source": "live", "prompt_version": "extract_v2", "status": "ok", "output_tokens": 1200}
    db.log_llm_run({**base, "model": "claude-sonnet-5", "input_tokens": 3000, "cache_write_tokens": 700,
                    "cache_read_tokens": 0, "cost_usd": 0.02, "latency_s": 11.0, "effort": "low"},
                   "extract", event_id, quote_id)
    db.log_llm_run({**base, "model": "claude-sonnet-5", "input_tokens": 3000, "cache_write_tokens": 0,
                    "cache_read_tokens": 700, "cost_usd": 0.018, "latency_s": 9.0}, "extract", event_id, quote_id)
    db.log_llm_run({**base, "model": "claude-opus-5", "input_tokens": 3500, "cost_usd": 0.05, "latency_s": 14.0},
                   "extract_escalation", event_id, quote_id)
    # Spend that is not extraction: a memo draft (no quote) and a test-quote writer call (tied to the quote).
    db.log_llm_run({**base, "model": "claude-sonnet-5", "prompt_version": "memo_v1", "input_tokens": 2000,
                    "cost_usd": 0.03, "latency_s": 8.0}, "memo", event_id)
    db.log_llm_run({**base, "model": "claude-sonnet-5", "prompt_version": "generate_v1", "input_tokens": 900,
                    "cost_usd": 0.04, "latency_s": 6.0}, "generate", event_id, quote_id)
    summary = {"config": "routed", "run_at": "2026-09-15 20:00 UTC", "source": "live Claude call",
               "model": "claude-sonnet-5", "prompt_version": "extract_v2", "effort": "low", "documents": 4,
               "trials": 3, "n": 12, "field_accuracy": 0.99, "field_accuracy_spread": {"mean": 0.99, "min": 0.97, "max": 1.0},
               "citation_rate": 1.0, "flag_recall": 1.0, "flag_precision": 1.0, "escalation_rate": 0.08,
               "cost_per_document_usd": 0.019, "avg_latency_s": 10.0, "latency_p50_s": 9.5, "latency_p95_s": 14.2,
               "cache_hit_rate": 0.42, "gate": {"fired_wrong": 1, "fired_ok": 0, "quiet_wrong": 0, "quiet_ok": 11,
                                                "precision": 1.0, "recall": 1.0, "false_fire_rate": 0.0, "n": 12}}
    (tmp_path / "results.json").write_text(json.dumps(summary), encoding="utf-8")
    lines = [{**summary, "git": "abc1234", "config": c, "cost_per_document_usd": cost}
             for c, cost in (("routed", 0.019), ("opus-only", 0.043))]
    (tmp_path / "history.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    yield
    db._conn.close()
    monkeypatch.setattr(db, "_conn", None)


def test_scorecard_renders_live_routing_cost_split_and_history(seeded):
    at = AppTest.from_file(str(ROOT / "pages" / "4_AI_Scorecard.py"), default_timeout=60).run()
    assert not at.exception, at.exception
    labels = {m.label for m in at.metric}
    assert {"Latency p50 / p95", "Prompt cache hit rate", "Quality gate fired", "Cost per document"} <= labels
    assert next(m for m in at.metric if m.label == "Prompt cache hit rate").value == "42%"
    assert next(m for m in at.metric if m.label == "Extractions escalated to stronger model").value == "50%"
    tables = [df for df in at.dataframe]
    assert any("cache_hit_rate" in df.value.columns for df in tables)     # routing table by model
    assert any("component" in df.value.columns for df in tables)          # cost split
    assert any("Eval history" in e.label for e in at.expander)


def test_cost_per_quote_counts_extraction_spend_only(seeded):
    at = AppTest.from_file(str(ROOT / "pages" / "4_AI_Scorecard.py"), default_timeout=60).run()
    assert not at.exception, at.exception
    metric = {m.label: m.value for m in at.metric}
    assert metric["Live API spend"] == "$0.16"         # everything: 0.02 + 0.018 + 0.05 extraction, 0.03 memo, 0.04 writer
    assert metric["API cost per quote"] == "$0.088"    # extraction and escalation only, over the 1 quote extracted live
