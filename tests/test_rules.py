import copy

from bidlens import rules
from bidlens.schemas import flat_values


def codes(flags):
    return {f.code for f in flags}


def peer_prices(samples):
    return [rules.unit_price_usd(flat_values(s["quote"])) for s in samples.values()]


def test_each_sample_raises_exactly_its_expected_flags(samples, rfq):
    peers = peer_prices(samples)
    for name, s in samples.items():
        found = codes(rules.evaluate(s["quote"], rfq, s["text"], peers))
        assert found == set(s["expected_flags"]), name


def test_fabricated_citation_is_flagged(samples, rfq):
    s = samples["lakeshore"]
    quote = copy.deepcopy(s["quote"])
    quote["unit_price"] = {"value": 199.0, "source_quote": "Unit Price: $199.00 each", "confidence": "high"}
    flags = rules.evaluate(quote, rfq, s["text"])
    assert any(f.code == "UNVERIFIED_SOURCE" and f.field == "unit_price" for f in flags)


def test_buyer_edited_value_is_not_treated_as_hallucination(samples, rfq):
    s = samples["sierra"]
    quote = copy.deepcopy(s["quote"])
    quote["warranty_months"] = {"value": 12.0, "source_quote": None, "confidence": "high", "edited": True}
    found = codes(rules.evaluate(quote, rfq, s["text"]))
    assert "UNVERIFIED_SOURCE" not in found
    assert "MISSING_FIELD" not in found


def test_prompt_injection_detected_only_where_present(samples, rfq):
    assert "SUSPICIOUS_INSTRUCTION" in codes(rules.evaluate(samples["jadeport"]["quote"], rfq, samples["jadeport"]["text"]))
    for name in ("kessler", "lakeshore", "sierra"):
        assert "SUSPICIOUS_INSTRUCTION" not in codes(rules.evaluate(samples[name]["quote"], rfq, samples[name]["text"]))


def test_price_outlier(samples, rfq):
    s = samples["lakeshore"]
    quote = copy.deepcopy(s["quote"])
    quote["unit_price"]["value"] = 400.0
    quote["unit_price"]["edited"] = True
    flags = rules.evaluate(quote, rfq, s["text"], [172.0, 184.8, 194.0, 400.0])
    assert "PRICE_OUTLIER" in codes(flags)


def test_flags_sorted_by_severity(samples, rfq):
    s = samples["kessler"]
    flags = rules.evaluate(s["quote"], rfq, s["text"])
    ranks = [rules.SEVERITY_ORDER[f.severity] for f in flags]
    assert ranks == sorted(ranks)
