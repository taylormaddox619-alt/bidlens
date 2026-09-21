"""Weighted multi-criteria scoring with a transparent breakdown."""

from dataclasses import dataclass

from .costing import LandedCost
from .schemas import RFQ

DEFAULT_WEIGHTS = {"cost": 50, "lead_time": 20, "terms": 15, "risk": 15}


@dataclass
class ScoredBid:
    quote_id: str
    supplier: str
    cost_score: float
    lead_time_score: float
    terms_score: float
    risk_score: float
    overall: float
    rationale: list[str]


def _terms_score(values: dict, rfq: RFQ, flag_codes: set[str]) -> tuple[float, list[str]]:
    """Commercial terms, 100 minus deductions.

    An expired quote loses 30 points here and, as a high flag, another 20 risk points in `score_bids`. The
    stacking is deliberate: terms measures the commercial offer, risk measures open exceptions.
    """
    score, why = 100.0, []
    days = values.get("payment_terms_days")
    if days is not None and days < rfq.standard_payment_days:
        penalty = min(30.0, (rfq.standard_payment_days - days) / 2)
        score -= penalty
        why.append(f"-{penalty:.0f} payment terms Net {days:g}")
    if values.get("prepayment_percent"):
        score -= 20
        why.append("-20 prepayment required")
    warranty = values.get("warranty_months")
    if warranty is None:
        score -= 25
        why.append("-25 warranty not stated")
    elif warranty < 12:
        score -= 15
        why.append("-15 warranty under 12 months")
    if "QUOTE_EXPIRED" in flag_codes:
        score -= 30
        why.append("-30 quote expired")
    return max(score, 0.0), why


def score_bids(bids: list[dict], rfq: RFQ, weights: dict | None = None) -> list[ScoredBid]:
    """`bids`: [{quote_id, values, landed: LandedCost, flags: [Flag dicts]}]."""
    weights = weights or DEFAULT_WEIGHTS
    total_w = sum(weights.values()) or 1
    w = {k: val / total_w for k, val in weights.items()}

    min_cost = min(b["landed"].landed_total_usd for b in bids)
    lead_times = [b["values"].get("lead_time_weeks") for b in bids if b["values"].get("lead_time_weeks")]
    min_lt = min(lead_times) if lead_times else None

    results = []
    for b in bids:
        landed: LandedCost = b["landed"]
        values = b["values"]
        flags = b["flags"]
        codes = {f["code"] for f in flags}
        rationale = []

        cost_score = 100 * min_cost / landed.landed_total_usd
        rationale.append(f"Cost: ${landed.landed_per_unit_usd:,.2f}/unit landed")

        lt = values.get("lead_time_weeks")
        if lt and min_lt:
            lt_score = 100 * min_lt / lt
            if lt > rfq.required_lead_time_weeks:
                lt_score -= 25
                rationale.append(f"Lead time: {lt:g} wks (-25 exceeds requirement)")
            else:
                rationale.append(f"Lead time: {lt:g} wks")
        else:
            lt_score = 0.0
            rationale.append("Lead time: not stated")
        lt_score = max(lt_score, 0.0)

        terms_score, terms_why = _terms_score(values, rfq, codes)
        rationale.extend(f"Terms: {t}" for t in terms_why)

        highs = sum(1 for f in flags if f["severity"] == "high")
        mediums = sum(1 for f in flags if f["severity"] == "medium")
        risk_score = max(100.0 - 20 * highs - 8 * mediums, 0.0)
        rationale.append(f"Risk: {highs} high / {mediums} medium flags")

        overall = (w["cost"] * cost_score + w["lead_time"] * lt_score
                   + w["terms"] * terms_score + w["risk"] * risk_score)
        results.append(ScoredBid(b["quote_id"], landed.supplier, cost_score, lt_score,
                                 terms_score, risk_score, overall, rationale))

    return sorted(results, key=lambda s: s.overall, reverse=True)
