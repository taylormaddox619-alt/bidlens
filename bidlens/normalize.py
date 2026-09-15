"""Deterministic normalization of extracted values into the formats the rules engine expects.

The model is asked to normalize, but models sometimes copy values as written ("15.12.2026",
"Made in USA"). A missed conversion is not cosmetic: an unparsed expiry date silently skips
the expired-quote check. This layer converts common variants and leaves anything it can't
interpret unchanged, so the rules still flag it for the buyer. The original wording stays
in `source_quote`.
"""

import re
from datetime import date, datetime

from .reference import country_code

INCOTERMS = ("EXW", "FCA", "FAS", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP")
DATE_FIELDS = ("quote_date", "valid_until")

_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y", "%d.%m.%y", "%B %d, %Y", "%b %d, %Y", "%B %d %Y",
                 "%b %d %Y", "%d %B %Y", "%d %b %Y", "%d-%b-%Y")
_COUNTRY_ALIASES = {
    "usa": "US", "u.s.a.": "US", "u.s.": "US", "us": "US", "united states of america": "US", "america": "US",
    "deutschland": "DE", "germany": "DE", "méxico": "MX", "mexico": "MX", "prc": "CN",
    "people's republic of china": "CN", "uk": "GB", "united kingdom": "GB", "great britain": "GB",
    "italia": "IT", "españa": "ES", "spain": "ES", "france": "FR", "india": "IN", "japan": "JP",
    "korea": "KR", "republic of korea": "KR", "viet nam": "VN", "canada": "CA", "brasil": "BR",
}
_CURRENCY_SYMBOLS = {"$": "USD", "US$": "USD", "USD$": "USD", "€": "EUR", "£": "GBP", "¥": "CNY", "RMB": "CNY",
                     "MX$": "MXN", "C$": "CAD", "₹": "INR"}


def normalize_date(value: str) -> str:
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", str(value).strip().rstrip("."))
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return value


def normalize_country(value: str) -> str:
    text = re.sub(r"\(.*?\)", "", str(value)).strip().strip(".,")
    text = re.sub(r"^(made in|origin:?|country of origin:?)\s+", "", text, flags=re.IGNORECASE).strip()
    if re.fullmatch(r"[A-Za-z]{2}", text):
        return text.upper()
    if text.lower() in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[text.lower()]
    return country_code(text) or value


def normalize_currency(value: str) -> str:
    text = str(value).strip()
    if text in _CURRENCY_SYMBOLS:
        return _CURRENCY_SYMBOLS[text]
    match = re.search(r"\b[A-Za-z]{3}\b", text)
    return match.group(0).upper() if match else value


def normalize_incoterm(value: str) -> str:
    match = re.search(r"\b(" + "|".join(INCOTERMS) + r")\b", str(value).upper())
    return match.group(1) if match else value


_NORMALIZERS = {"country_of_origin": normalize_country, "currency": normalize_currency,
                "incoterm": normalize_incoterm, **{f: normalize_date for f in DATE_FIELDS}}


def normalize_quote(quote: dict) -> tuple[dict, list[tuple[str, str, str]]]:
    """Return (normalized quote, [(field, before, after)]) without mutating the input."""
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in quote.items()}
    changes = []
    for field, fn in _NORMALIZERS.items():
        value = (out.get(field) or {}).get("value")
        if not isinstance(value, str) or not value.strip():
            continue
        new = fn(value)
        if new != value:
            out[field]["value"] = new
            changes.append((field, value, new))
    return out, changes


def is_iso_date(value) -> bool:
    try:
        date.fromisoformat(str(value))
        return True
    except ValueError:
        return False
