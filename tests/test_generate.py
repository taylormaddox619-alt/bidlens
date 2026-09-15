"""AI-generated test quotes: fact generation, rendering, answer-key grading, and the public cap. No API calls."""

import random
from datetime import date

import pytest

from bidlens import config, costing, db, generate, rules, workflow
from bidlens.grading import compare_fields
from bidlens.ingest import extract_text
from bidlens.schemas import RFQ, Quote, flat_values


@pytest.fixture
def rfq_dict():
    return generate.random_rfq(random.Random(7), date(2026, 9, 15))


def suppliers(rfq_dict, n=40, seed=1):
    rng, used = random.Random(seed), set()
    return [generate.random_supplier(rng, rfq_dict, i, used) for i in range(n)]


def test_answer_keys_are_valid_quotes_the_pipeline_can_cost(rfq_dict):
    rfq = RFQ.model_validate(rfq_dict)
    for s in suppliers(rfq_dict):
        Quote.model_validate(s.truth)
        lc = costing.landed_cost(flat_values(s.truth), rfq)
        assert lc.landed_total_usd > 0
        rules.evaluate(s.truth, rfq, "")  # expected flags computable without citations


def test_tier_price_matches_unit_price_at_rfq_quantity(rfq_dict):
    tiered = [s for s in suppliers(rfq_dict, 60) if s.truth["price_tiers"]]
    assert tiered, "seed should produce some tiered quotes"
    for s in tiered:
        values = flat_values(s.truth)
        assert costing.applicable_unit_price(values, rfq_dict["quantity"]) == s.truth["unit_price"]["value"]


def test_scenarios_vary(rfq_dict):
    batch = suppliers(rfq_dict, 40)
    assert len({s.truth["country_of_origin"]["value"] for s in batch}) >= 4
    assert len({s.truth["incoterm"]["value"] for s in batch}) >= 4
    assert {"pdf", "xlsx"} <= {s.doc_format for s in batch}
    assert len({s.truth["supplier_name"]["value"] for s in batch}) == len(batch)


def test_fact_strings_cover_every_graded_answer(rfq_dict):
    for s in suppliers(rfq_dict):
        text = " ".join(str(v) for v in s.facts.values())
        assert generate.missing_strings(text, s.required_strings) == []


def fake_document(supplier):
    lines = [supplier.facts["supplier_name"], "QUOTATION"] + [
        f"{k.replace('_', ' ').title()}: {v if not isinstance(v, list) else '; '.join(v)}"
        for k, v in supplier.facts.items()]
    if supplier.doc_format == "xlsx":
        return generate.GeneratedDocument(lines=[], table_rows=[[line] for line in lines])
    return generate.GeneratedDocument(lines=lines, table_rows=[])


def test_rendered_documents_contain_all_required_facts(rfq_dict):
    for s in suppliers(rfq_dict, 12):
        rendered = generate.render(s, fake_document(s))
        assert generate.missing_strings(extract_text(s.filename, rendered), s.required_strings) == [], s.filename


def test_grading_scores_misses_and_skips(rfq_dict):
    s = suppliers(rfq_dict, 1)[0]
    extracted = {k: (dict(v) if isinstance(v, dict) else v) for k, v in s.truth.items()}
    assert all(f["correct"] for f in compare_fields(s.truth, extracted))
    extracted["lead_time_weeks"] = {"value": 99.0}
    misses = [f["field"] for f in compare_fields(s.truth, extracted) if not f["correct"]]
    assert misses == ["lead_time_weeks"]
    assert "lead_time_weeks" not in [f["field"] for f in compare_fields(s.truth, extracted, skip={"lead_time_weeks"})]


def test_generation_workflow_end_to_end_without_api(tmp_path, monkeypatch, rfq_dict):
    """Writer and reader are faked; checks storage, answer keys, scoring, and the public cap counter."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "gen.duckdb")
    monkeypatch.setattr(db, "_conn", None)

    def fake_build(api_key, rfq, count, seed):
        batch = suppliers(rfq, count, seed=3)
        for s in batch:
            s.document = generate.render(s, fake_document(s))
            s.writer_meta = {"status": "ok", "model": "claude-sonnet-5", "cost_usd": 0.01, "source": "live",
                             "purpose": "generate", "input_tokens": 1, "output_tokens": 1}
        return batch

    def fake_extract(text, rfq, mode, api_key):
        truth = next(s.truth for s in suppliers(rfq_dict, 4, seed=3) if s.facts["quote_number"] in text)
        quote = {k: (dict(v) if isinstance(v, dict) else v) for k, v in truth.items()}
        quote["moq"] = {"value": 1.0, "source_quote": None, "confidence": "high"}  # one deliberate miss each
        return quote, {"source": "live", "status": "ok", "model": "claude-sonnet-5", "cost_usd": 0.02}

    monkeypatch.setattr(generate, "build_test_quotes", fake_build)
    monkeypatch.setattr(workflow, "extract", fake_extract)

    rfq = RFQ.model_validate(rfq_dict)
    event_id = db.create_event(rfq_dict, "tester")
    before = db.public_generations_today()
    outcome = workflow.generate_test_quotes(event_id, rfq, "key", "tester", privileged=False)

    assert len(outcome["results"]) == config.QUOTES_PER_GENERATION
    assert all(s["misses"] == ["MOQ"] or "moq" in s["omitted_by_writer"] for s in outcome["scores"])
    assert outcome["cost_usd"] == pytest.approx(config.QUOTES_PER_GENERATION * 0.03)
    quote_id = outcome["results"][0]["quote_id"]
    assert db.get_answer_key(quote_id)["truth"]["quote_number"]["value"]
    assert db.public_generations_today() == before + 1

    workflow.generate_test_quotes(event_id, rfq, "key", "tester", privileged=True)
    assert db.public_generations_today() == before + 1  # passcode holders don't use the public allowance
