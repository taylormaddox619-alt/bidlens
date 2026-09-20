"""Deterministic normalization of extracted values into the formats the rules engine expects.

The model is asked to normalize, but models sometimes copy values as written ("15.12.2026",
"Made in USA"). A missed conversion is not cosmetic: an unparsed expiry date silently skips
the expired-quote check. This layer converts common variants and leaves anything it can't
interpret unchanged, so the rules still flag it for the buyer. The original wording stays
in `source_quote`.
"""

import re
from datetime import date, datetime

from .reference import country_code, fx_table

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
# Currency names the model may copy as written. These are unambiguous, so they match anywhere in the text,
# longest phrase first ("canadian dollars" before anything shorter).
_CURRENCY_NAMES = tuple(
    (re.compile(r"\b" + phrase.replace(" ", r"\s+") + r"\b", re.IGNORECASE), code)
    for phrase, code in sorted({
        r"united states dollars?": "USD", r"u\.?s\.? dollars?": "USD", r"canadian dollars?": "CAD",
        r"mexican pesos?": "MXN", r"indian rupees?": "INR", r"pounds? sterling": "GBP", r"sterling": "GBP",
        r"euros?": "EUR", r"renminbi": "CNY", r"yuan": "CNY", r"yen": "JPY",
    }.items(), key=lambda item: len(item[0]), reverse=True)
)
# Many countries have a dollar, peso, pound or rupee. The bare word is read as the usual one only when it is
# the whole value; "Australian dollars" stays as written so the gate and the UNKNOWN_CURRENCY rule catch it.
_BARE_CURRENCY_WORDS = {"dollar": "USD", "dollars": "USD", "peso": "MXN", "pesos": "MXN", "pound": "GBP",
                        "pounds": "GBP", "rupee": "INR", "rupees": "INR"}


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
    """ISO code for a currency written as a code, symbol or name; anything else is returned unchanged."""
    text = str(value).strip()
    # A supported ISO code anywhere in the text wins ("Dollars (USD)"). Only codes with an FX rate count, so
    # an ordinary three-letter word ("the euro", "Yen") is never mistaken for a code.
    for token in re.findall(r"\b[A-Za-z]{3}\b", text):
        if token.upper() in fx_table():
            return token.upper()
    if text in _CURRENCY_SYMBOLS:
        return _CURRENCY_SYMBOLS[text]
    for pattern, code in _CURRENCY_NAMES:
        if pattern.search(text):
            return code
    return _BARE_CURRENCY_WORDS.get(text.strip(".,").lower(), value)


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
