"""Prompt caching, effort and cost accounting in the live extractor. No API calls: the SDK client is stubbed."""

import copy
from types import SimpleNamespace

import pytest

from bidlens import config, extract
from bidlens.schemas import Quote


def test_cost_prices_cached_tokens_separately():
    price_in, price_out = config.MODEL_PRICING["claude-sonnet-5"]
    plain = extract.cost_usd("claude-sonnet-5", 1000, 500)
    assert plain == pytest.approx((1000 * price_in + 500 * price_out) / 1e6)
    cached = extract.cost_usd("claude-sonnet-5", 1000, 500, cache_write_tokens=800, cache_read_tokens=1600)
    expected = plain + (800 * price_in * 1.25 + 1600 * price_in * 0.10) / 1e6
    assert cached == pytest.approx(expected)
    # A cache read is far cheaper than sending the same tokens uncached.
    assert extract.cost_usd("claude-sonnet-5", 0, 0, cache_read_tokens=1000) < extract.cost_usd("claude-sonnet-5", 1000, 0)


def test_unknown_model_priced_at_top_tier_including_cache():
    top_in, top_out = max(config.MODEL_PRICING.values())
    assert extract.cost_usd("claude-mystery-9", 100, 10, 50, 50) == pytest.approx(
        (100 * top_in + 10 * top_out + 50 * top_in * 1.25 + 50 * top_in * 0.10) / 1e6)


class FakeUsage(SimpleNamespace):
    pass


@pytest.fixture
def stub_client(monkeypatch, samples):
    """Replace anthropic.Anthropic with a client that records parse() kwargs and returns a canned response."""
    calls = []
    quote = copy.deepcopy(samples["jadeport"]["quote"])

    class Messages:
        def parse(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                usage=FakeUsage(input_tokens=3000, output_tokens=1200,
                                cache_creation_input_tokens=0 if len(calls) > 1 else 700,
                                cache_read_input_tokens=700 if len(calls) > 1 else 0),
                model=kwargs["model"], _request_id=f"req_{len(calls)}", stop_reason="end_turn",
                parsed_output=Quote.model_validate(quote),
            )

    class Client:
        def __init__(self, api_key=None):
            self.beta = SimpleNamespace(messages=Messages())

    monkeypatch.setattr(extract.anthropic, "Anthropic", Client)
    return calls


def test_system_prompt_is_marked_for_caching(stub_client, samples, rfq):
    _, meta = extract.live_extraction(samples["jadeport"]["text"], rfq, "key", "claude-sonnet-5")
    system = stub_client[0]["system"]
    assert isinstance(system, list) and system[0]["cache_control"] == {"type": "ephemeral"}
    assert system[0]["text"] == extract.load_prompt(config.EXTRACT_PROMPT_VERSION)
    # The document is in the user turn, after the cached prefix.
    assert samples["jadeport"]["text"][:40] in stub_client[0]["messages"][0]["content"]
    assert meta["status"] == "ok"


def test_effort_sent_only_when_set(stub_client, samples, rfq):
    text = samples["jadeport"]["text"]
    extract.live_extraction(text, rfq, "key", "claude-sonnet-5")
    assert "output_config" not in stub_client[0]
    _, meta = extract.live_extraction(text, rfq, "key", "claude-sonnet-5", effort="low")
    assert stub_client[1]["output_config"] == {"effort": "low"}
    assert meta["effort"] == "low"


def test_meta_carries_cache_tokens_and_priced_cost(stub_client, samples, rfq):
    text = samples["jadeport"]["text"]
    _, first = extract.live_extraction(text, rfq, "key", "claude-sonnet-5")
    _, second = extract.live_extraction(text, rfq, "key", "claude-sonnet-5")
    assert (first["cache_write_tokens"], first["cache_read_tokens"]) == (700, 0)
    assert (second["cache_write_tokens"], second["cache_read_tokens"]) == (0, 700)
    assert first["cost_usd"] == pytest.approx(extract.cost_usd("claude-sonnet-5", 3000, 1200, 700, 0))
    assert second["cost_usd"] == pytest.approx(extract.cost_usd("claude-sonnet-5", 3000, 1200, 0, 700))
    assert second["cost_usd"] < first["cost_usd"]
    assert first["request_id"] == "req_1"


def test_routed_passes_per_route_effort(monkeypatch, samples, rfq):
    seen = []

    def fake_live(text, rfq, api_key, model, prompt_version, effort=None):
        seen.append((model, effort))
        weak = copy.deepcopy(samples["lakeshore"]["quote"])
        if model == "cheap":
            weak["unit_price"]["source_quote"] = "not in the document"
        return weak, {"source": "live", "model": model, "status": "ok", "input_tokens": 1, "output_tokens": 1,
                      "cache_write_tokens": 10, "cache_read_tokens": 20, "cost_usd": 0.01, "latency_s": 1.0}

    monkeypatch.setattr(extract, "live_extraction", fake_live)
    routes = {"extract": "cheap", "extract_escalation": "strong"}
    _, meta = extract.routed_extraction(samples["lakeshore"]["text"], rfq, "key", routes=routes,
                                        efforts={"extract": "low", "extract_escalation": "high"})
    assert seen == [("cheap", "low"), ("strong", "high")]
    assert meta["escalated"] and meta["cache_write_tokens"] == 20 and meta["cache_read_tokens"] == 40
