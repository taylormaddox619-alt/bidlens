"""Reference data: illustrative tariff rates, freight estimates, and FX rates."""

import csv
from functools import lru_cache

from .config import REFERENCE_DIR


@lru_cache(maxsize=1)
def tariff_table() -> dict[str, dict]:
    with open(REFERENCE_DIR / "tariff_rates.csv", newline="", encoding="utf-8") as f:
        return {
            row["country_code"]: {
                "country_name": row["country_name"],
                "tariff_rate": float(row["tariff_rate"]),
                "est_freight_per_unit_usd": float(row["est_freight_per_unit_usd"]),
            }
            for row in csv.DictReader(f)
        }


@lru_cache(maxsize=1)
def fx_table() -> dict[str, float]:
    with open(REFERENCE_DIR / "fx_rates.csv", newline="", encoding="utf-8") as f:
        return {row["currency"]: float(row["usd_per_unit"]) for row in csv.DictReader(f)}


def fx_as_of() -> str:
    with open(REFERENCE_DIR / "fx_rates.csv", newline="", encoding="utf-8") as f:
        return next(csv.DictReader(f))["as_of"]


def country_code(value: str | None) -> str | None:
    """Accept an ISO code or a country name and return the ISO code if known."""
    if not value:
        return None
    v = value.strip()
    table = tariff_table()
    if v.upper() in table:
        return v.upper()
    for code, row in table.items():
        if row["country_name"].lower() == v.lower():
            return code
    return None
