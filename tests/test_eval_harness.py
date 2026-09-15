"""Eval harness aggregation: trials, percentiles, gate 2x2, calibration, history. No API calls."""

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))

import run_evals  # noqa: E402
from bidlens import config  # noqa: E402


def doc(filename, fields, *, latency, cost, fired=False, escalated=False, tokens=(1000, 500, 0, 0)):
    inp, out, cw, cr = tokens
    return {"filename": filename, "model": "m", "escalated": escalated, "cost_usd": cost, "latency_s": latency,
            "effort": None, "input_tokens": inp, "output_tokens": out, "cache_write_tokens": cw,
            "cache_read_tokens": cr, "status": "ok", "fields": fields, "cites_total": len(fields),
            "cites_ok": len(fields), "flags_found": [], "flags_expected": [], "gate_fired": fired,
            "gate_reasons": ["x"] if fired else []}


def field(name, correct=True, confidence="high"):
    return {"field": name, "label": name, "expected": 1, "actual": 1 if correct else 2, "correct": correct,
            "confidence": confidence, "cited": True}


def trial(docs, accuracy):
    return {"config": "t", "label": "test config", "source": "live Claude call", "model": "m",
            "prompt_version": "extract_v2", "field_accuracy": accuracy, "citation_rate": 1.0,
            "flag_recall": 1.0, "flag_precision": 1.0, "documents_detail": docs}


def test_percentile_interpolates_like_numpy():
    xs = [10, 20, 30, 40]
    assert run_evals.percentile(xs, 0.5) == 25
    assert run_evals.percentile(xs, 0.95) == pytest.approx(38.5)
    assert run_evals.percentile([7], 0.95) == 7
    assert run_evals.percentile([], 0.5) == 0.0


def test_aggregate_reports_spread_percentiles_and_cache_hit():
    t1 = trial([doc("a", [field("f")], latency=10, cost=0.02, tokens=(1000, 500, 600, 0)),
                doc("b", [field("f")], latency=20, cost=0.03, tokens=(1000, 500, 0, 600))], accuracy=1.0)
    t2 = trial([doc("a", [field("f", correct=False)], latency=30, cost=0.02, tokens=(1000, 500, 0, 600)),
                doc("b", [field("f")], latency=40, cost=0.03, tokens=(1000, 500, 0, 600))], accuracy=0.5)
    s = run_evals.aggregate([t1, t2])
    assert s["trials"] == 2 and s["n"] == 4 and s["documents"] == 2
    assert s["field_accuracy"] == pytest.approx(0.75)
    assert s["field_accuracy_spread"] == {"mean": 0.75, "min": 0.5, "max": 1.0}
    assert s["latency_p50_s"] == 25 and s["latency_p95_s"] == pytest.approx(38.5)
    assert s["cost_per_document_usd"] == pytest.approx(0.025)
    # 1800 cache reads out of 4000 uncached + 600 written + 1800 read
    assert s["cache_hit_rate"] == pytest.approx(1800 / 6400)
    assert s["per_field"] == {"f": 0.75}
    assert s["trials_detail"] is not None and len(s["trials_detail"]) == 2
    for key in ("field_accuracy", "citation_rate", "flag_recall", "flag_precision", "escalation_rate",
                "cost_per_document_usd", "avg_latency_s", "run_at", "source", "model", "prompt_version", "documents"):
        assert key in s  # the scorecard reads these


def test_gate_two_by_two():
    docs = [doc("a", [field("f", correct=False)], latency=1, cost=0, fired=True),   # caught
            doc("b", [field("f", correct=False)], latency=1, cost=0, fired=False),  # missed
            doc("c", [field("f")], latency=1, cost=0, fired=True),                  # needless
            doc("d", [field("f")], latency=1, cost=0, fired=False),
            doc("e", [field("f")], latency=1, cost=0, fired=False)]
    g = run_evals.gate_stats(docs)
    assert (g["fired_wrong"], g["fired_ok"], g["quiet_wrong"], g["quiet_ok"]) == (1, 1, 1, 2)
    assert g["precision"] == 0.5 and g["recall"] == 0.5 and g["false_fire_rate"] == pytest.approx(0.2)
    assert run_evals.gate_stats([doc("d", [field("f")], latency=1, cost=0)])["precision"] is None


def test_calibration_buckets_by_confidence_and_orders_levels():
    docs = [doc("a", [field("f", confidence="low"), field("g", correct=False, confidence="low"),
                      field("h", confidence="high"), field("i", correct=True, confidence="medium")], latency=1, cost=0)]
    c = run_evals.calibration(docs)
    assert list(c) == ["high", "medium", "low"]
    assert c["low"] == {"count": 2, "correct_rate": 0.5} and c["high"]["count"] == 1


def test_demo_run_writes_demo_files_and_leaves_history_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EVALS_DIR", tmp_path)
    (tmp_path / "history.jsonl").write_text("", encoding="utf-8")
    summaries = run_evals.main(["--demo"])
    assert (tmp_path / "results_demo.json").exists() and (tmp_path / "report_demo.md").exists()
    assert not (tmp_path / "results.json").exists()
    assert (tmp_path / "history.jsonl").read_text(encoding="utf-8") == ""
    s = summaries[0]
    assert s["documents"] == 4 and s["field_accuracy"] == 1.0 and s["citation_rate"] == 1.0
    assert s["flag_recall"] == 1.0
    assert "n=4" in (tmp_path / "report_demo.md").read_text(encoding="utf-8")


def test_history_line_has_metrics_but_not_documents(tmp_path):
    s = run_evals.aggregate([trial([doc("a", [field("f")], latency=1, cost=0.01)], 1.0)])
    path = tmp_path / "history.jsonl"
    run_evals.append_history([s, copy.deepcopy(s)], path)
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2
    assert lines[0]["config"] == "test config" and "field_accuracy" in lines[0] and "latency_p95_s" in lines[0]
    assert "documents_detail" not in lines[0] and "trials_detail" not in lines[0]
