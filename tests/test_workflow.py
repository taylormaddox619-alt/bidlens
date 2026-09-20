"""Workflow orchestration against a temporary database: comparison never raises on an uncostable quote."""

import copy

import pytest

from bidlens import costing, db, rules, workflow
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
