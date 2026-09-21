"""Normalization of values models copy as written. Cases come from real Sonnet 5 output in the eval."""

import copy

import pytest

from bidlens import extract, rules
from bidlens.normalize import normalize_country, normalize_currency, normalize_date, normalize_incoterm, normalize_quote


@pytest.mark.parametrize("raw, expected", [
    ("15.12.2026", "2026-12-15"), ("August 31, 2026", "2026-08-31"), ("2026-10-31", "2026-10-31"),
    ("Aug 31, 2026", "2026-08-31"), ("31 August 2026", "2026-08-31"), ("August 31st, 2026", "2026-08-31"),
    ("sometime soon", "sometime soon"),
])
def test_dates(raw, expected):
    assert normalize_date(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("China", "CN"), ("Germany", "DE"), ("USA", "US"), ("Mexico (USMCA qualifying)", "MX"),
    ("Deutschland (Germany)", "DE"), ("Made in USA", "US"), ("cn", "CN"), ("Atlantis", "Atlantis"),
    ("UK", "GB"), ("uk", "GB"),  # two letters, but not the ISO code: the alias must win over the shortcut
])
def test_countries(raw, expected):
    assert normalize_country(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("$", "USD"), ("€", "EUR"), ("eur", "EUR"), ("USD", "USD"),
    ("US Dollars", "USD"), ("United States Dollars", "USD"), ("Euro", "EUR"), ("Euros", "EUR"),
    ("Mexican pesos", "MXN"), ("Canadian dollars", "CAD"), ("the euro", "EUR"), ("Yen", "JPY"),
    ("Dollars (USD)", "USD"), ("Dollars", "USD"), ("pounds sterling", "GBP"), ("Renminbi", "CNY"),
    ("Atlantean shells", "Atlantean shells"),
])
def test_currency(raw, expected):
    assert normalize_currency(raw) == expected


def test_currency_does_not_take_the_first_three_letter_word():
    """The old regex turned 'the euro' into 'THE': an unknown currency that reached the comparison."""
    assert normalize_currency("the euro") != "THE"
    assert normalize_currency("Yen") != "YEN"


@pytest.mark.parametrize("raw", ["Australian dollars", "Philippine pesos", "Egyptian pounds", "Pakistani rupees"])
def test_ambiguous_currency_words_are_not_guessed(raw):
    """A bare 'dollars' means USD only when it is the whole value; another nation's dollar must stay unknown
    so the gate and the UNKNOWN_CURRENCY rule catch it instead of costing it at the wrong FX rate."""
    assert normalize_currency(raw) == raw


@pytest.mark.parametrize("raw, expected", [("FOB Ningbo", "FOB"), ("Delivered DAP Charlotte", "DAP"),
                                           ("exw", "EXW"), ("ex works", "ex works")])
def test_incoterm(raw, expected):
    assert normalize_incoterm(raw) == expected


def test_real_sonnet_output_restores_expired_quote_flag(samples, rfq):
    """Sonnet returned 'August 31, 2026' and 'USA' for Lakeshore; unnormalized, the expiry check was skipped."""
    s = samples["lakeshore"]
    raw = copy.deepcopy(s["quote"])
    raw["valid_until"]["value"] = "August 31, 2026"
    raw["country_of_origin"]["value"] = "USA"
    before = {f.code for f in rules.evaluate(raw, rfq, s["text"])}
    assert "QUOTE_EXPIRED" not in before and "UNKNOWN_ORIGIN" in before

    fixed, changes = normalize_quote(raw)
    after = {f.code for f in rules.evaluate(fixed, rfq, s["text"])}
    assert "QUOTE_EXPIRED" in after and "UNKNOWN_ORIGIN" not in after
    assert ("valid_until", "August 31, 2026", "2026-08-31") in changes
    assert raw["valid_until"]["value"] == "August 31, 2026"  # input not mutated


def test_unreadable_values_escalate(samples):
    s = samples["kessler"]
    quote = copy.deepcopy(s["quote"])
    quote["valid_until"]["value"] = "mid-December"
    quote["country_of_origin"]["value"] = "Bavaria"
    reasons = extract.escalation_reasons(quote, {}, s["text"])
    assert any("valid_until" in r for r in reasons) and any("country_of_origin" in r for r in reasons)
