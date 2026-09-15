import pytest

from bidlens import costing, rules
from bidlens.schemas import flat_values
from bidlens.scoring import score_bids


def test_tier_price_selected_for_rfq_quantity(samples):
    values = flat_values(samples["sierra"]["quote"])
    assert costing.applicable_unit_price(values, 500) == 194.0
    assert costing.applicable_unit_price(values, 300) == 199.0
    assert costing.applicable_unit_price(values, 10) == 205.0


def test_landed_cost_components_jadeport(samples, rfq):
    lc = costing.landed_cost(flat_values(samples["jadeport"]["quote"]), rfq)
    assert lc.goods_usd == pytest.approx(86_000)
    assert lc.tooling_usd == pytest.approx(6_000)
    assert lc.freight_usd == pytest.approx(14 * 500)  # FOB -> lane estimate
    assert lc.freight_estimated
    assert lc.duty_usd == pytest.approx(86_000 * 0.35)
    assert lc.terms_adjustment_usd == pytest.approx(0)
    assert lc.landed_total_usd == pytest.approx(129_100)


def test_fx_conversion_and_prepayment_cost(samples, rfq):
    lc = costing.landed_cost(flat_values(samples["kessler"]["quote"]), rfq)
    assert lc.unit_price_usd == pytest.approx(168 * 1.10)
    assert lc.terms_adjustment_usd > 0  # Net 30 + 30% deposit costs more than Net 60


def test_freight_included_incoterm(samples, rfq):
    lc = costing.landed_cost(flat_values(samples["lakeshore"]["quote"]), rfq)
    assert lc.freight_usd == 0 and not lc.freight_estimated
    assert lc.landed_total_usd == pytest.approx(107_000)


def test_ddp_includes_duty(samples, rfq):
    values = flat_values(samples["jadeport"]["quote"])
    values["incoterm"] = "DDP"
    lc = costing.landed_cost(values, rfq)
    assert lc.duty_usd == 0 and lc.freight_usd == 0


def _bids(samples, rfq):
    bids = []
    for name, s in samples.items():
        values = flat_values(s["quote"])
        flags = [f.to_dict() for f in rules.evaluate(s["quote"], rfq, s["text"])]
        bids.append({"quote_id": name, "values": values, "flags": flags, "landed": costing.landed_cost(values, rfq)})
    return bids


def test_lowest_unit_price_is_not_lowest_landed_cost(samples, rfq):
    bids = _bids(samples, rfq)
    lowest_unit = min(bids, key=lambda b: b["landed"].unit_price_usd)
    lowest_landed = min(bids, key=lambda b: b["landed"].landed_total_usd)
    assert lowest_unit["quote_id"] == "jadeport"
    assert lowest_landed["quote_id"] == "sierra"


def test_default_weights_rank_sierra_first(samples, rfq):
    ranked = score_bids(_bids(samples, rfq), rfq)
    assert ranked[0].quote_id == "sierra"
    assert all(0 <= s.overall <= 100 for s in ranked)


def test_weights_change_ranking(samples, rfq):
    ranked = score_bids(_bids(samples, rfq), rfq, {"cost": 0, "lead_time": 0, "terms": 100, "risk": 0})
    assert ranked[0].quote_id == "jadeport"  # only quote with standard terms, no prepayment, warranty stated
