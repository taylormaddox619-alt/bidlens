import copy
import random
from datetime import date

import pytest

from bidlens import generate, rules
from bidlens.review import apply_edits, display_value
from bidlens.schemas import FIELD_SPECS


def grid(quote: dict, **overrides) -> list[dict]:
    """Simulate the review grid: every field shown as display text, with optional buyer changes."""
    return [{"field": name, "Value": overrides.get(name, display_value((quote.get(name) or {}).get("value")))}
            for name, *_ in FIELD_SPECS]


def untouched(quote: dict):
    return apply_edits(quote, grid(quote), copy.deepcopy(quote["price_tiers"]),
                       "\n".join(quote["supplier_exceptions"]))


def test_no_changes_produces_no_edits(samples):
    # Guards against false "unsaved edits" that would block approval.
    for s in samples.values():
        _, edits, errors = untouched(s["quote"])
        assert edits == [] and errors == [], s["filename"]


def test_grid_nan_values_are_treated_as_blank(samples):
    quote = samples["sierra"]["quote"]
    tiers = [{**t, "max_qty": float("nan") if t["max_qty"] is None else t["max_qty"]} for t in quote["price_tiers"]]
    _, edits, _ = apply_edits(quote, grid(quote, warranty_months=float("nan")), tiers, "")
    assert edits == []


def test_buyer_fills_missing_warranty_and_flag_clears(samples, rfq):
    s = samples["sierra"]
    new, edits, errors = apply_edits(s["quote"], grid(s["quote"], warranty_months="12"),
                                     s["quote"]["price_tiers"], "")
    assert errors == []
    assert edits == [("warranty_months", None, 12.0)]
    assert new["warranty_months"]["edited"] is True
    assert "MISSING_FIELD" not in {f.code for f in rules.evaluate(new, rfq, s["text"])}


def test_invalid_values_are_rejected(samples):
    quote = samples["kessler"]["quote"]
    _, edits, errors = apply_edits(quote, grid(quote, unit_price="about 170", valid_until="15.12.2026"),
                                   quote["price_tiers"], "\n".join(quote["supplier_exceptions"]))
    assert len(errors) == 2 and edits == []
    assert any("YYYY-MM-DD" in e for e in errors)


def test_currency_formatted_numbers_accepted(samples):
    quote = samples["jadeport"]["quote"]
    new, edits, _ = apply_edits(quote, grid(quote, tooling_cost="$6,500"), [],
                                "\n".join(quote["supplier_exceptions"]))
    assert new["tooling_cost"]["value"] == 6500.0
    assert edits == [("tooling_cost", 6000.0, 6500.0)]


def test_added_tier_is_marked_buyer_entered(samples):
    quote = samples["jadeport"]["quote"]
    new, edits, _ = apply_edits(quote, grid(quote), [{"min_qty": 1000, "max_qty": None, "unit_price": 165.0,
                                                      "source_quote": None}],
                                "\n".join(quote["supplier_exceptions"]))
    assert new["price_tiers"][0]["edited"] is True
    assert edits[0][0] == "price_tiers"


@pytest.mark.parametrize("value", [172.0, 0.1, 125000.75, 1234567.0, 17416.67, 356666.67, 85181.82, 27983.45,
                                   8.571428, 0.001, 2.5e9])
def test_display_value_round_trips(value):
    """The grid text is parsed back on every render; any rounding here shows up as an edit nobody made."""
    assert float(display_value(value)) == value


def test_display_value_formats():
    assert display_value(172.0) == "172"
    assert display_value(1234567.0) == "1234567"
    assert display_value(125000.75) == "125000.75"
    assert display_value(None) == ""
    assert display_value("EUR") == "EUR"


def test_generated_answer_keys_produce_no_phantom_edits():
    """Generated MXN and INR quotes have 7+ significant digits; '%g' formatting rounded 30 of these 200."""
    rfq = generate.random_rfq(random.Random(7), date(2026, 9, 15))
    rng, used, phantom = random.Random(1), set(), []
    for i in range(200):
        truth = generate.random_supplier(rng, rfq, i, used).truth
        _, edits, errors = untouched(truth)
        if edits or errors:
            phantom.append((i, edits, errors))
    assert phantom == []
