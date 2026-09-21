"""Workflow orchestration against a temporary database: comparison never raises on an uncostable quote."""

import copy

import pytest

from bidlens import costing, db, rules, workflow
from bidlens.config import SAMPLES_DIR
from bidlens.normalize import normalize_quote
from bidlens.schemas import flat_values
from bidlens.scoring import DEFAULT_WEIGHTS


@pytest.fixture
def event_id(tmp_path, monkeypatch, rfq):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "workflow.duckdb")
    monkeypatch.setattr(db, "_conn", None)
    yield db.create_event(rfq.model_dump(), "tester")
    db._conn.close()
    monkeypatch.setattr(db, "_conn", None)


def add_approved(event_id, sample, rfq, quote=None) -> str:
    quote = quote or sample["quote"]
    flags = [f.to_dict() for f in rules.evaluate(quote, rfq, sample["text"])]
    quote_id = db.add_quote(event_id, sample["filename"], None, sample["text"], quote, flags, "extracted", "tester")
    db.set_quote_status(quote_id, "approved", "tester", event_id, "")
    return quote_id


# --- Item 1: a quote that cannot be costed is excluded, not fatal ----------------------------------
def test_comparison_excludes_an_approved_quote_with_an_unknown_currency(event_id, samples, rfq):
    """The original report. Rows like this were stored and approved before currency names were normalized
    and before the review page checked for an FX rate, so they exist in deployed databases."""
    quote = copy.deepcopy(samples["lakeshore"]["quote"])
    quote["currency"]["value"] = "US Dollars"  # stored as the old normalizer left it
    bad = add_approved(event_id, samples["lakeshore"], rfq, quote)
    good = add_approved(event_id, samples["sierra"], rfq)

    rows = workflow.comparison(event_id, rfq, DEFAULT_WEIGHTS)
    assert [r["quote_id"] for r in rows] == [good]

    excluded = workflow.excluded_from_comparison(event_id, rfq)
    assert [x["quote_id"] for x in excluded] == [bad]
    assert "Lakeshore" in excluded[0]["supplier"]
    assert excluded[0]["filename"] == samples["lakeshore"]["filename"]
    assert excluded[0]["reason"] == "no FX rate on file for US DOLLARS"


def test_currency_written_as_words_is_now_costed(event_id, samples, rfq):
    """A new extraction that says 'US Dollars' is normalized to USD on the way in, and ranks."""
    quote = copy.deepcopy(samples["lakeshore"]["quote"])
    quote["currency"]["value"] = "US Dollars"
    quote, _ = normalize_quote(quote)
    add_approved(event_id, samples["lakeshore"], rfq, quote)
    add_approved(event_id, samples["sierra"], rfq)
    assert len(workflow.comparison(event_id, rfq, DEFAULT_WEIGHTS)) == 2
    assert workflow.excluded_from_comparison(event_id, rfq) == []


def test_comparison_excludes_a_zero_unit_price(event_id, samples, rfq):
    quote = copy.deepcopy(samples["jadeport"]["quote"])
    quote["unit_price"]["value"] = 0.0
    quote["price_tiers"] = []
    add_approved(event_id, samples["jadeport"], rfq, quote)
    add_approved(event_id, samples["sierra"], rfq)
    assert len(workflow.comparison(event_id, rfq, DEFAULT_WEIGHTS)) == 1  # used to be ZeroDivisionError in scoring
    assert workflow.excluded_from_comparison(event_id, rfq)[0]["reason"] == "unit price must be greater than zero"


def test_every_sample_is_costable(samples, rfq):
    for s in samples.values():
        assert costing.uncostable(flat_values(s["quote"]), rfq.quantity) is None


@pytest.mark.parametrize("change, reason", [
    ({"currency": None}, "currency not stated"),
    ({"currency": "US Dollars"}, "no FX rate on file for US DOLLARS"),
    ({"unit_price": None, "price_tiers": []}, "unit price not stated"),
    ({"unit_price": 0.0, "price_tiers": []}, "unit price must be greater than zero"),
])
def test_uncostable_reasons_and_landed_cost_agree(samples, rfq, change, reason):
    values = {**flat_values(samples["jadeport"]["quote"]), **change}
    assert costing.uncostable(values, rfq.quantity) == reason
    with pytest.raises(ValueError, match=reason):
        costing.landed_cost(values, rfq)


# --- Item 3: duplicate documents ---------------------------------------------------------------------
def sample_files() -> list[tuple[str, bytes]]:
    return [(p.name, p.read_bytes()) for p in sorted(SAMPLES_DIR.iterdir()) if p.suffix in (".pdf", ".xlsx")]


@pytest.fixture
def counted_extract(monkeypatch):
    """Count calls to the extractor: duplicates must be dropped before any (paid) model call."""
    calls = []
    real = workflow.extract

    def counting(text, rfq, mode, api_key=None):
        calls.append(text)
        return real(text, rfq, mode, api_key)

    monkeypatch.setattr(workflow, "extract", counting)
    return calls


def test_processing_the_same_documents_twice_stores_them_once(event_id, rfq, counted_extract):
    first = workflow.process_documents(event_id, rfq, sample_files(), "demo", None, "tester")
    assert [r["status"] for r in first] == ["extracted"] * 4 and len(counted_extract) == 4

    second = workflow.process_documents(event_id, rfq, sample_files(), "demo", None, "tester")
    assert [r["status"] for r in second] == ["duplicate"] * 4
    assert all(r["quote_id"] is None and r["cost_usd"] == 0.0 for r in second)
    assert second[0]["error"] == f"Identical to {first[0]['filename']}, already in this event."
    assert len(counted_extract) == 4  # no extraction for duplicates
    quotes = db.list_quotes(event_id)
    assert len(quotes) == 4 and len({q["doc_sha"] for q in quotes}) == 4


def test_the_same_file_twice_in_one_batch_is_stored_once(event_id, rfq, counted_extract):
    name, data = sample_files()[0]
    results = workflow.process_documents(event_id, rfq, [(name, data), ("copy_" + name, data)], "demo", None, "t")
    assert [r["status"] for r in results] == ["extracted", "duplicate"]
    assert results[1]["error"] == f"Identical to {name}, already in this event."
    assert len(db.list_quotes(event_id)) == 1 and len(counted_extract) == 1


def test_single_document_path_also_skips_duplicates(event_id, rfq):
    name, data = sample_files()[0]
    assert workflow.process_document(event_id, rfq, name, data, "demo", None, "t")["status"] == "extracted"
    assert workflow.process_document(event_id, rfq, name, data, "demo", None, "t")["status"] == "duplicate"


def test_a_failed_document_can_be_retried(event_id, rfq, monkeypatch):
    """A failed extraction (rate limit, API error) must not block uploading the same file again."""
    name, data = sample_files()[0]
    with monkeypatch.context() as m:
        m.setattr(workflow, "extract", lambda *a: (None, {"source": "live", "status": "failed",
                                                          "error": "Rate limited"}))
        assert workflow.process_document(event_id, rfq, name, data, "live", "key", "t")["status"] == "failed"
    assert workflow.process_document(event_id, rfq, name, data, "demo", None, "t")["status"] == "extracted"
    assert sorted(q["status"] for q in db.list_quotes(event_id)) == ["extracted", "failed"]
