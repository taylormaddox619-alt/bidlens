"""Run the Streamlit pages headlessly (AppTest) against a temporary database. No API calls."""

import copy
import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from bidlens import db, rules, workflow
from bidlens.config import SAMPLES_DIR
from bidlens.scoring import DEFAULT_WEIGHTS

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ["Home.py"] + [f"views/{p.name}" for p in sorted((ROOT / "views").glob("*.py"))]


@pytest.fixture
def event_id(tmp_path, monkeypatch, rfq):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "pages.duckdb")
    monkeypatch.setattr(db, "_conn", None)
    yield db.create_event(rfq.model_dump(), "tester")
    db._conn.close()
    monkeypatch.setattr(db, "_conn", None)


def run_page(script: str, event_id: str | None = None, **state) -> AppTest:
    at = AppTest.from_file(str(ROOT / script), default_timeout=60)
    if event_id:
        at.session_state["event_id"] = event_id
    for key, value in state.items():
        at.session_state[key] = value
    return at.run()


def add_quote(event_id, sample, rfq, quote=None, status="extracted") -> str:
    quote = quote or sample["quote"]
    flags = [f.to_dict() for f in rules.evaluate(quote, rfq, sample["text"])]
    quote_id = db.add_quote(event_id, sample["filename"], None, sample["text"], quote, flags, "extracted", "tester")
    if status != "extracted":
        db.set_quote_status(quote_id, status, "tester", event_id, "")
    return quote_id


def button(at: AppTest, text: str):
    return next(b for b in at.button if text in b.label)


# --- Item 1: an approved quote with an unrecognized currency ------------------------------------
@pytest.fixture
def words_currency(samples):
    """A quote as the old normalizer stored it: the currency written out instead of an ISO code."""
    quote = copy.deepcopy(samples["lakeshore"]["quote"])
    quote["currency"]["value"] = "US Dollars"
    return quote


def test_comparison_page_warns_instead_of_crashing(event_id, samples, rfq, words_currency):
    add_quote(event_id, samples["lakeshore"], rfq, words_currency, "approved")
    add_quote(event_id, samples["sierra"], rfq, status="approved")
    add_quote(event_id, samples["jadeport"], rfq, status="approved")
    at = run_page("views/3_Comparison.py", event_id)
    assert not at.exception, at.exception
    assert any("Lakeshore" in w.value and "no FX rate on file for US DOLLARS" in w.value for w in at.warning)
    assert any(m.value.startswith("#### Scorecard") for m in at.markdown)  # the other two are still compared


def test_comparison_page_needs_two_costable_quotes(event_id, samples, rfq, words_currency):
    add_quote(event_id, samples["lakeshore"], rfq, words_currency, "approved")
    add_quote(event_id, samples["sierra"], rfq, status="approved")
    at = run_page("views/3_Comparison.py", event_id)
    assert not at.exception, at.exception
    assert any("Lakeshore" in w.value for w in at.warning)
    assert any("At least two approved quotes" in w.value for w in at.warning)


def test_review_page_blocks_approval_of_an_uncostable_quote(event_id, samples, rfq, words_currency):
    quote_id = add_quote(event_id, samples["lakeshore"], rfq, words_currency)
    at = run_page("views/2_Review_Approve.py", event_id, review_quote=quote_id)
    assert not at.exception, at.exception
    at.checkbox(key=f"ack_{quote_id}").check().run()  # accepting the high flags must not unlock it
    assert not at.exception, at.exception
    assert button(at, "Approve quote").disabled
    assert any(c.value.startswith("Approval blocked: no FX rate on file for US DOLLARS") for c in at.caption)


def test_review_page_still_approves_a_normal_quote(event_id, samples, rfq):
    quote_id = add_quote(event_id, samples["sierra"], rfq)
    at = run_page("views/2_Review_Approve.py", event_id, review_quote=quote_id)
    assert not at.exception, at.exception
    if at.checkbox:
        at.checkbox(key=f"ack_{quote_id}").check().run()
    assert not button(at, "Approve quote").disabled
    assert not any(c.value.startswith("Approval blocked") for c in at.caption)


# --- Item 2: an untouched grid is not an edit ------------------------------------------------------
def test_review_page_shows_no_phantom_edits_for_long_numbers(event_id, samples, rfq):
    quote = copy.deepcopy(samples["sierra"]["quote"])
    quote["tooling_cost"]["value"] = 125000.75  # 8 significant digits; '%g' showed 125001
    quote_id = add_quote(event_id, samples["sierra"], rfq, quote)
    at = run_page("views/2_Review_Approve.py", event_id, review_quote=quote_id)
    assert not at.exception, at.exception
    assert not any("unsaved edits" in w.value for w in at.warning)
    if at.checkbox:
        at.checkbox(key=f"ack_{quote_id}").check().run()
    assert not button(at, "Approve quote").disabled


# --- Item 3: two quotes from the same supplier ----------------------------------------------------
def test_comparison_page_keeps_same_name_quotes_apart(event_id, samples, rfq):
    revised = copy.deepcopy(samples["sierra"]["quote"])
    revised["unit_price"]["value"] = 150.0
    revised["price_tiers"] = []
    first = add_quote(event_id, samples["sierra"], rfq, status="approved")
    second = add_quote(event_id, {**samples["sierra"], "filename": "Q4_revised.xlsx"}, rfq, revised, "approved")
    rows = workflow.comparison(event_id, rfq, DEFAULT_WEIGHTS)
    runner_up = rows[1]["quote_id"]
    assert {first, second} == {r["quote_id"] for r in rows}

    at = run_page("views/3_Comparison.py", event_id)
    assert not at.exception, at.exception
    picker = next(s for s in at.selectbox if s.label == "Award to")
    assert len(set(picker.options)) == 2 and all("Sierra Madre" in o for o in picker.options)
    assert any(samples["sierra"]["filename"] in o for o in picker.options)

    picker.set_value(runner_up).run()
    at.text_area[0].input("Revised quote withdrawn by the supplier.").run()
    button(at, "Record award decision").click().run()
    assert not at.exception, at.exception
    award = db.get_award(event_id)
    assert award["quote_id"] == runner_up  # by name, this resolved to the first quote
    assert award["followed_recommendation"] is False


# --- Governance: dollar amounts are text, not LaTeX ---------------------------------------------------
def test_governance_page_escapes_dollar_signs(event_id):
    """Two unescaped '$' on one line ('$0.0116/doc ... $0.0306') rendered everything between them as math."""
    at = run_page("views/5_Governance.py", event_id)
    assert not at.exception, at.exception
    doc = next(m.value for m in at.markdown if "Solution inventory" in m.value or "Model routing" in m.value)
    assert "\\$0.0116" in doc
    assert not re.search(r"(?<!\\)\$", doc)


# --- Smoke: every page renders in every workflow state ----------------------------------------------
def load_demo_event(event_id, rfq) -> None:
    files = [(p.name, p.read_bytes()) for p in sorted(SAMPLES_DIR.iterdir()) if p.suffix in (".pdf", ".xlsx")]
    workflow.process_documents(event_id, rfq, files, "demo", None, "tester")
    workflow.refresh_flags(event_id, rfq)


def award_demo_event(event_id, rfq) -> None:
    for q in db.list_quotes(event_id):
        db.set_quote_status(q["id"], "approved", "tester", event_id, "")
    rows = workflow.comparison(event_id, rfq, DEFAULT_WEIGHTS)
    best, naive = rows[0], min(rows, key=lambda r: r["landed"].unit_price_usd)
    db.record_award({"event_id": event_id, "quote_id": best["quote_id"], "supplier": best["supplier"],
                     "landed_total_usd": best["landed"].landed_total_usd, "naive_supplier": naive["supplier"],
                     "naive_landed_total_usd": naive["landed"].landed_total_usd,
                     "savings_vs_naive_usd": naive["landed"].landed_total_usd - best["landed"].landed_total_usd,
                     "followed_recommendation": True}, "tester")


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("state", ["empty", "loaded", "awarded"])
def test_every_page_renders_in_every_state(event_id, rfq, script, state):
    if state == "empty":
        db.delete_event(event_id, "tester")
    else:
        load_demo_event(event_id, rfq)
    if state == "awarded":
        award_demo_event(event_id, rfq)
    at = run_page(script, None if state == "empty" else event_id)
    assert not at.exception, at.exception
