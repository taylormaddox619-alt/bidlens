"""Data contracts for RFQs and extracted supplier quotes.

Every extracted value carries the verbatim text it came from and a confidence
rating, so a buyer can verify it and the rules engine can detect fabricated
citations.
"""

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field

Confidence = Literal["high", "medium", "low"]

_SOURCE_DESC = (
    "Verbatim text copied exactly from the document that supports the value. "
    "null when the value is null."
)


class TextField(BaseModel):
    value: Optional[str]
    source_quote: Optional[str] = Field(description=_SOURCE_DESC)
    confidence: Confidence


class NumberField(BaseModel):
    value: Optional[float]
    source_quote: Optional[str] = Field(description=_SOURCE_DESC)
    confidence: Confidence


class PriceTier(BaseModel):
    min_qty: int
    max_qty: Optional[int] = Field(description="null when the tier is open-ended")
    unit_price: float
    source_quote: str


class Quote(BaseModel):
    supplier_name: TextField
    quote_number: TextField
    quote_date: TextField = Field(description="ISO date YYYY-MM-DD")
    valid_until: TextField = Field(description="ISO date YYYY-MM-DD the quote expires")
    currency: TextField = Field(description="ISO 4217 code, e.g. USD, EUR")
    unit_price: NumberField = Field(
        description="Unit price that applies at the RFQ quantity (use the matching tier)"
    )
    price_tiers: list[PriceTier] = Field(description="Quantity price breaks; empty if none")
    tooling_cost: NumberField = Field(description="One-time tooling/pattern/setup charges")
    freight_cost: NumberField = Field(
        description="Total quoted freight charge. null if freight is excluded or not stated"
    )
    incoterm: TextField = Field(description="Incoterms 2020 code only, e.g. FOB, EXW, DAP")
    incoterm_location: TextField
    country_of_origin: TextField = Field(description="ISO 3166-1 alpha-2 code, e.g. CN, DE")
    lead_time_weeks: NumberField
    payment_terms: TextField = Field(description="Payment terms as written")
    payment_terms_days: NumberField = Field(description="Days until payment is due, e.g. 60")
    prepayment_percent: NumberField = Field(
        description="Percent of order value due before shipment (deposit, advance, or cash in advance); "
                    "null when the document states none"
    )
    warranty_months: NumberField
    moq: NumberField = Field(description="Minimum order quantity")
    supplier_exceptions: list[str] = Field(
        description="Assumptions, exclusions, or exceptions the supplier states"
    )


class RFQ(BaseModel):
    event_name: str
    item: str
    quantity: int
    currency: str = "USD"
    required_lead_time_weeks: float
    standard_payment_days: int = 60
    destination: str
    evaluation_date: date


# Field metadata drives the review UI and the required-field rules.
# (name, label, kind, required)
FIELD_SPECS: list[tuple[str, str, str, bool]] = [
    ("supplier_name", "Supplier", "text", True),
    ("quote_number", "Quote #", "text", False),
    ("quote_date", "Quote date", "date", False),
    ("valid_until", "Valid until", "date", True),
    ("currency", "Currency", "text", True),
    ("unit_price", "Unit price (at RFQ qty)", "number", True),
    ("tooling_cost", "Tooling / one-time cost", "number", False),
    ("freight_cost", "Freight cost (total)", "number", False),
    ("incoterm", "Incoterm", "text", True),
    ("incoterm_location", "Incoterm location", "text", False),
    ("country_of_origin", "Country of origin", "text", True),
    ("lead_time_weeks", "Lead time (weeks)", "number", True),
    ("payment_terms", "Payment terms", "text", True),
    ("payment_terms_days", "Payment days", "number", True),
    ("prepayment_percent", "Prepayment %", "number", False),
    ("warranty_months", "Warranty (months)", "number", True),
    ("moq", "MOQ", "number", False),
]

FIELD_LABELS = {name: label for name, label, _, _ in FIELD_SPECS}
FIELD_LABELS.update(price_tiers="Price breaks", supplier_exceptions="Supplier exceptions")


def flat_values(quote: dict) -> dict:
    """Map a Quote dict to {field: value} for rules and costing."""
    out = {name: (quote.get(name) or {}).get("value") for name, _, _, _ in FIELD_SPECS}
    out["price_tiers"] = quote.get("price_tiers") or []
    out["supplier_exceptions"] = quote.get("supplier_exceptions") or []
    return out
