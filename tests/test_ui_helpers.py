"""Pure guidance helpers in bidlens.ui: risk badges and next-step selection."""

from bidlens import ui

HIGH = {"severity": "high", "code": "QUOTE_EXPIRED", "field": "valid_until", "message": "expired"}
MED = {"severity": "medium", "code": "FREIGHT_ESTIMATED", "field": "freight_cost", "message": "estimated"}
LOW = {"severity": "low", "code": "SUPPLIER_EXCEPTIONS", "field": "supplier_exceptions", "message": "note"}


def quote(status, flags=()):
    return {"status": status, "flags": list(flags), "filename": "q.pdf", "reviewed": None}


def test_risk_badge_and_level():
    assert ui.risk_badge([HIGH, HIGH, MED]) == "🔴 2 high · 🟠 1 medium"
    assert ui.risk_badge([HIGH, MED], compact=True) == "🔴1 🟠1"
    assert ui.risk_badge([LOW]) == "🟢 No risks flagged"  # info alone is not a risk
    assert ui.risk_level([MED, LOW]) == "medium"
    assert ui.risk_level([]) == "none"


def test_next_step_walks_the_workflow():
    assert ui.next_step([], None)[0] == "setup"
    page, label = ui.next_step([quote("extracted", [HIGH]), quote("approved"), quote("extracted")], None)
    assert page == "review" and label == "Next: review 2 quotes →"
    assert ui.next_step([quote("extracted"), quote("approved")], None)[1] == "Next: review 1 quote →"
    assert ui.next_step([quote("approved"), quote("rejected")], None)[0] == "setup"  # need 2 approved
    assert ui.next_step([quote("approved"), quote("approved")], None)[0] == "compare"
    assert ui.next_step([quote("approved"), quote("approved")], {"supplier": "X"})[0] == "scorecard"


def test_display_names():
    assert ui.short_name("JADEPORT FOUNDRY CO., LTD.") == "Jadeport Foundry"
    assert ui.short_name("Kessler & Vogt Gusstechnik GmbH") == "Kessler & Vogt Gusstechnik"
    assert ui.display_name("ABC Castings Inc.") == "ABC Castings Inc."  # mixed case left alone


def test_every_page_key_maps_to_a_real_page():
    from bidlens.config import ROOT
    for path in ui.PAGES.values():
        assert (ROOT / path).exists(), path
