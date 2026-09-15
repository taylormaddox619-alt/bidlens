"""Model routing: cheap first pass, escalate only on quality failures. No API calls."""

import copy

import pytest

from bidlens import extract

ROUTES = {"extract": "cheap-model", "extract_escalation": "strong-model", "memo": "cheap-model"}


def run_meta(model, cost, **extra):
    return {"source": "live", "model": model, "prompt_version": "extract_v1", "input_tokens": 1000,
            "output_tokens": 500, "cache_write_tokens": 100, "cache_read_tokens": 0, "cost_usd": cost,
            "latency_s": 2.0, **extra}


@pytest.fixture
def fake_models(monkeypatch):
    """Script what each model returns: {model: (quote or None, extra meta)}."""
    calls = []

    def install(responses):
        def fake_live(text, rfq, api_key, model, prompt_version, effort=None):
            calls.append(model)
            quote, extra = responses[model]
            status = {"status": "ok"} if quote else {"status": "failed"}
            return copy.deepcopy(quote), run_meta(model, 0.01 if model == "cheap-model" else 0.05, **status, **extra)
        monkeypatch.setattr(extract, "live_extraction", fake_live)
        return calls

    return install


def test_good_first_pass_is_accepted(samples):
    s = samples["jadeport"]
    assert extract.escalation_reasons(s["quote"], {}, s["text"]) == []


def test_fabricated_citation_triggers_escalation(samples):
    s = samples["lakeshore"]
    quote = copy.deepcopy(s["quote"])
    quote["lead_time_weeks"]["source_quote"] = "Lead Time: 10 weeks ARO"
    assert any("lead_time_weeks" in r for r in extract.escalation_reasons(quote, {}, s["text"]))


def test_low_confidence_required_field_triggers_escalation(samples):
    s = samples["kessler"]
    quote = copy.deepcopy(s["quote"])
    quote["payment_terms_days"]["confidence"] = "low"
    assert any("low confidence" in r for r in extract.escalation_reasons(quote, {}, s["text"]))


def test_low_confidence_optional_field_does_not_escalate(samples):
    s = samples["kessler"]
    quote = copy.deepcopy(s["quote"])
    quote["moq"]["confidence"] = "low"
    assert extract.escalation_reasons(quote, {}, s["text"]) == []


def test_routed_uses_only_cheap_model_when_checks_pass(samples, rfq, fake_models):
    s = samples["sierra"]
    calls = fake_models({"cheap-model": (s["quote"], {})})
    quote, meta = extract.routed_extraction(s["text"], rfq, "key", routes=ROUTES)
    assert calls == ["cheap-model"]
    assert meta["escalated"] is False and meta["model"] == "cheap-model"
    assert quote["unit_price"]["value"] == 194.0


def test_routed_escalates_and_sums_cost(samples, rfq, fake_models):
    s = samples["lakeshore"]
    weak = copy.deepcopy(s["quote"])
    weak["unit_price"]["source_quote"] = "Unit Price: $199.00 each"
    calls = fake_models({"cheap-model": (weak, {}), "strong-model": (s["quote"], {})})
    quote, meta = extract.routed_extraction(s["text"], rfq, "key", routes=ROUTES)
    assert calls == ["cheap-model", "strong-model"]
    assert meta["escalated"] and meta["model"] == "strong-model"
    assert meta["cost_usd"] == pytest.approx(0.06)
    assert meta["cache_write_tokens"] == 200  # summed across both passes
    assert [r["purpose"] for r in meta["runs"]] == ["extract", "extract_escalation"]
    assert quote["unit_price"]["source_quote"] == "Unit Price: $214.00 each"


def test_infra_errors_do_not_escalate(samples, rfq, fake_models):
    s = samples["jadeport"]
    calls = fake_models({"cheap-model": (None, {"error": "Rate limited", "error_kind": "infra"})})
    quote, meta = extract.routed_extraction(s["text"], rfq, "key", routes=ROUTES)
    assert calls == ["cheap-model"] and quote is None and not meta["escalated"]


def test_capability_failure_escalates(samples, rfq, fake_models):
    s = samples["kessler"]
    calls = fake_models({"cheap-model": (None, {"error": "schema", "error_kind": "capability"}),
                         "strong-model": (s["quote"], {})})
    quote, meta = extract.routed_extraction(s["text"], rfq, "key", routes=ROUTES)
    assert calls == ["cheap-model", "strong-model"] and quote is not None and meta["status"] == "ok"


def test_failed_escalation_keeps_first_pass(samples, rfq, fake_models):
    s = samples["lakeshore"]
    weak = copy.deepcopy(s["quote"])
    weak["unit_price"]["source_quote"] = "not in the document"
    fake_models({"cheap-model": (weak, {}), "strong-model": (None, {"error": "API error 529"})})
    quote, meta = extract.routed_extraction(s["text"], rfq, "key", routes=ROUTES)
    assert quote == weak and meta["model"] == "cheap-model" and meta["escalation_error"] == "API error 529"


def test_fallbacks_only_sent_to_supported_models():
    assert extract.fallback_kwargs("claude-opus-5")["fallbacks"] == "default"
    assert extract.fallback_kwargs("claude-sonnet-5") == {}
