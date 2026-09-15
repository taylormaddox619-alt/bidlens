"""Normalize quotes to a total landed cost in the RFQ currency (USD)."""

from dataclasses import asdict, dataclass

from . import config
from .reference import country_code, fx_table, tariff_table
from .schemas import RFQ


@dataclass
class LandedCost:
    supplier: str
    currency: str
    unit_price_quoted: float
    unit_price_usd: float
    goods_usd: float
    tooling_usd: float
    freight_usd: float
    freight_estimated: bool
    duty_usd: float
    tariff_rate: float
    terms_adjustment_usd: float
    landed_total_usd: float
    landed_per_unit_usd: float
    notes: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def applicable_unit_price(values: dict, quantity: int) -> float | None:
    """Pick the tier price for the RFQ quantity; fall back to the stated unit price."""
    for tier in sorted(values.get("price_tiers") or [], key=lambda t: t["min_qty"], reverse=True):
        max_qty = tier.get("max_qty")
        if quantity >= tier["min_qty"] and (max_qty is None or quantity <= max_qty):
            return float(tier["unit_price"])
    price = values.get("unit_price")
    return float(price) if price is not None else None


def landed_cost(values: dict, rfq: RFQ) -> LandedCost:
    notes: list[str] = []
    qty = rfq.quantity
    currency = (values.get("currency") or rfq.currency).upper()
    fx = fx_table().get(currency)
    if fx is None:
        raise ValueError(f"No FX rate for {currency}")
    if currency != rfq.currency:
        notes.append(f"Converted {currency}->USD at {fx}")

    unit_quoted = applicable_unit_price(values, qty)
    if unit_quoted is None:
        raise ValueError("Unit price is required to compute landed cost")
    unit_usd = unit_quoted * fx
    goods = unit_usd * qty
    tooling = float(values.get("tooling_cost") or 0) * fx

    incoterm = (values.get("incoterm") or "").upper()
    cc = country_code(values.get("country_of_origin"))
    lane = tariff_table().get(cc) if cc else None

    freight_estimated = False
    if values.get("freight_cost") is not None:
        freight = float(values["freight_cost"]) * fx
    elif incoterm in config.FREIGHT_INCLUDED_INCOTERMS:
        freight = 0.0
        notes.append(f"Freight included ({incoterm})")
    else:
        per_unit = lane["est_freight_per_unit_usd"] if lane else 12.0
        freight = per_unit * qty
        freight_estimated = True
        notes.append(f"Freight estimated at ${per_unit:.2f}/unit")

    tariff_rate = lane["tariff_rate"] if lane else 0.0
    if incoterm in config.DUTY_INCLUDED_INCOTERMS:
        duty = 0.0
        notes.append("Duty included (DDP)")
    else:
        duty = goods * tariff_rate
        if not lane:
            notes.append("Origin unknown - duty not estimated")

    # Value payment-term differences as the cost of capital tied up (or freed).
    r = config.COST_OF_CAPITAL
    days = values.get("payment_terms_days")
    days = float(days) if days is not None else float(rfq.standard_payment_days)
    terms_adj = goods * r * (rfq.standard_payment_days - days) / 365
    prepay = float(values.get("prepayment_percent") or 0) / 100
    if prepay:
        lead_days = float(values.get("lead_time_weeks") or 0) * 7
        terms_adj += goods * prepay * r * (lead_days + days) / 365
    if abs(terms_adj) >= 1:
        notes.append(f"Payment terms adjustment at {r:.0%} cost of capital")

    total = goods + tooling + freight + duty + terms_adj
    return LandedCost(
        supplier=values.get("supplier_name") or "Unknown supplier",
        currency=currency,
        unit_price_quoted=unit_quoted,
        unit_price_usd=unit_usd,
        goods_usd=goods,
        tooling_usd=tooling,
        freight_usd=freight,
        freight_estimated=freight_estimated,
        duty_usd=duty,
        tariff_rate=tariff_rate,
        terms_adjustment_usd=terms_adj,
        landed_total_usd=total,
        landed_per_unit_usd=total / qty,
        notes=notes,
    )
