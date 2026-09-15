"""Deterministic exception checks. No LLM involved - these are auditable business rules."""

import re
import statistics
from dataclasses import asdict, dataclass
from datetime import date

from . import config
from .ingest import quote_in_document
from .reference import country_code, fx_table, tariff_table
from .schemas import FIELD_LABELS, FIELD_SPECS, RFQ, flat_values

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

# Phrases that suggest a document is trying to instruct an AI reviewer.
_INJECTION_PATTERNS = [
    r"\b(ignore|disregard)\b.{0,40}\b(instructions|other (bids|quotes|suppliers))\b",
    r"\b(ai|automated|llm)\b.{0,30}\b(reviewer|system|assistant)s?\b",
    r"\brecommend (this|our) (supplier|company|quote)\b",
]


@dataclass
class Flag:
    code: str
    severity: str  # high | medium | low
    field: str | None
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def unit_price_usd(values: dict) -> float | None:
    price, cur = values.get("unit_price"), (values.get("currency") or "").upper()
    rate = fx_table().get(cur)
    if price is None or rate is None:
        return None
    return float(price) * rate


def evaluate(quote: dict, rfq: RFQ, document_text: str, peer_unit_prices_usd=None) -> list[Flag]:
    """Run every rule against one quote. `quote` is a Quote dict (with source quotes)."""
    v = flat_values(quote)
    flags: list[Flag] = []

    # Required fields
    for name, label, _, required in FIELD_SPECS:
        if required and v.get(name) in (None, ""):
            sev = "high" if name in ("unit_price", "currency", "supplier_name") else "medium"
            flags.append(Flag("MISSING_FIELD", sev, name, "Not stated in the quote - confirm with supplier."))

    # Citation check: every non-null value must be backed by text that exists in the document
    for name, label, _, _ in FIELD_SPECS:
        field = quote.get(name) or {}
        if field.get("value") in (None, "") or field.get("edited"):
            continue  # buyer-entered values are attributed to the buyer, not the document
        src = field.get("source_quote")
        if not src or not quote_in_document(src, document_text):
            flags.append(Flag("UNVERIFIED_SOURCE", "high", name,
                              "Cited text not found in document - possible hallucination, verify manually."))
        elif field.get("confidence") == "low":
            flags.append(Flag("LOW_CONFIDENCE", "medium", name, "Model reported low confidence - verify."))
    for tier in v["price_tiers"]:
        if not tier.get("edited") and not quote_in_document(tier.get("source_quote") or "", document_text):
            flags.append(Flag("UNVERIFIED_SOURCE", "high", "price_tiers",
                              f"Price tier from {tier.get('min_qty')} units: cited text not found in document."))

    cur = (v.get("currency") or "").upper()
    if cur and cur != rfq.currency.upper():
        flags.append(Flag("CURRENCY_MISMATCH", "medium", "currency",
                          f"Quoted in {cur}, RFQ requested {rfq.currency}. Converted at reference FX rate; FX risk applies."))
    if cur and cur not in fx_table():
        flags.append(Flag("UNKNOWN_CURRENCY", "high", "currency", f"No FX rate on file for {cur}."))

    lt = v.get("lead_time_weeks")
    if lt is not None and lt > rfq.required_lead_time_weeks:
        flags.append(Flag("LEAD_TIME_EXCEEDS", "high", "lead_time_weeks",
                          f"Lead time {lt:g} wks exceeds required {rfq.required_lead_time_weeks:g} wks."))

    valid = _parse_date(v.get("valid_until"))
    if valid and valid < rfq.evaluation_date:
        flags.append(Flag("QUOTE_EXPIRED", "high", "valid_until",
                          f"Quote expired {valid.isoformat()} - request a revalidated quote before award."))

    days = v.get("payment_terms_days")
    if days is not None and days < rfq.standard_payment_days:
        flags.append(Flag("PAYMENT_TERMS_BELOW_STANDARD", "medium", "payment_terms_days",
                          f"Net {days:g} is shorter than standard Net {rfq.standard_payment_days}."))
    prepay = v.get("prepayment_percent")
    if prepay:
        flags.append(Flag("PREPAYMENT_REQUIRED", "medium", "prepayment_percent",
                          f"{prepay:g}% prepayment required - adds cash and supplier-default risk."))

    incoterm = (v.get("incoterm") or "").upper()
    if incoterm and incoterm not in config.FREIGHT_INCLUDED_INCOTERMS and v.get("freight_cost") is None:
        flags.append(Flag("FREIGHT_ESTIMATED", "medium", "freight_cost",
                          f"{incoterm}: freight excluded and not quoted - landed cost uses a lane estimate."))

    cc = country_code(v.get("country_of_origin"))
    if v.get("country_of_origin") and cc is None:
        flags.append(Flag("UNKNOWN_ORIGIN", "medium", "country_of_origin",
                          "Country of origin not in tariff table - duty not estimated."))
    elif cc:
        rate = tariff_table()[cc]["tariff_rate"]
        if rate >= config.HIGH_TARIFF_THRESHOLD and incoterm not in config.DUTY_INCLUDED_INCOTERMS:
            flags.append(Flag("TARIFF_EXPOSURE", "medium", "country_of_origin",
                              f"Origin {cc} carries an estimated {rate:.0%} tariff."))

    moq = v.get("moq")
    if moq is not None and moq > rfq.quantity:
        flags.append(Flag("MOQ_ABOVE_QTY", "medium", "moq", f"MOQ {moq:g} exceeds RFQ quantity {rfq.quantity}."))

    price_usd = unit_price_usd(v)
    peers = [p for p in (peer_unit_prices_usd or []) if p]
    if price_usd and len(peers) >= 3:
        median = statistics.median(peers)
        deviation = (price_usd - median) / median
        if abs(deviation) > config.PRICE_OUTLIER_THRESHOLD:
            flags.append(Flag("PRICE_OUTLIER", "medium", "unit_price",
                              f"Unit price is {deviation:+.0%} vs. peer median - check scope or quantity basis."))

    if v["supplier_exceptions"]:
        flags.append(Flag("SUPPLIER_EXCEPTIONS", "low", "supplier_exceptions",
                          f"Supplier stated {len(v['supplier_exceptions'])} exception(s)/assumption(s) - review."))

    if any(re.search(p, document_text, re.IGNORECASE) for p in _INJECTION_PATTERNS):
        flags.append(Flag("SUSPICIOUS_INSTRUCTION", "high", None,
                          "Document contains text that appears to instruct an AI reviewer. It was treated as data; "
                          "review the document and escalate per procurement policy."))

    return sorted(flags, key=lambda f: SEVERITY_ORDER[f.severity])


def label(field: str | None) -> str:
    return FIELD_LABELS.get(field, field or "Document")
