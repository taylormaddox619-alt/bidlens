"""Award lifecycle: a recorded award must not outlive the approval it was based on."""

import pytest

from bidlens import db


@pytest.fixture
def event(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "awards.duckdb")
    monkeypatch.setattr(db, "_conn", None)
    event_id = db.create_event({"event_name": "RFQ test", "item": "bracket", "quantity": 100, "currency": "USD",
                                "required_lead_time_weeks": 8.0, "standard_payment_days": 60,
                                "destination": "Charlotte", "evaluation_date": "2026-09-15"}, "tester")
    quote_id = db.add_quote(event_id, "q.pdf", "sha", "text", {"supplier_name": {"value": "Acme"}}, [],
                            "approved", "tester")
    db.record_award({"event_id": event_id, "quote_id": quote_id, "supplier": "Acme", "landed_total_usd": 1000.0,
                     "naive_supplier": "Acme", "naive_landed_total_usd": 1000.0, "savings_vs_naive_usd": 0.0,
                     "followed_recommendation": True}, "tester")
    return event_id, quote_id


def test_withdraw_award_clears_award_and_reopens_event(event):
    event_id, quote_id = event
    assert db.get_event(event_id)["status"] == "awarded"

    withdrawn = db.withdraw_award(event_id, "tester", "quote reopened")
    assert withdrawn["supplier"] == "Acme"
    assert db.get_award(event_id) is None
    assert db.get_event(event_id)["status"] == "open"
    actions = [row["action"] for row in db.audit_log(event_id)]
    assert "award_withdrawn" in actions


def test_withdraw_without_award_is_a_no_op(event):
    event_id, _ = event
    db.withdraw_award(event_id, "tester", "first")
    assert db.withdraw_award(event_id, "tester", "second") is None
