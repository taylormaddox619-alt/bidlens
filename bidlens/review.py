"""Apply buyer corrections to an extracted quote, with type validation and an edit trail."""

import copy
import math
from datetime import date

from .rules import label
from .schemas import FIELD_SPECS

_TIER_KEYS = ("min_qty", "max_qty", "unit_price")


def _blank(value) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value)) or value == ""


def _parse(kind: str, raw: str):
    if raw == "":
        return None
    if kind == "number":
        return float(raw.replace(",", "").replace("$", "").replace("%", ""))
    if kind == "date":
        return date.fromisoformat(raw).isoformat()
    return raw


def apply_edits(reviewed: dict, field_rows: list[dict], tier_rows: list[dict],
                exceptions_text: str) -> tuple[dict, list[tuple], list[str]]:
    """Return (updated quote, [(field, old, new)], [validation errors]).

    `field_rows`: [{"field": name, "Value": str}] as shown in the review grid.
    `tier_rows`: [{"min_qty", "max_qty", "unit_price", "source_quote"}].
    """
    new = copy.deepcopy(reviewed)
    edits, errors = [], []
    kinds = {name: kind for name, _, kind, _ in FIELD_SPECS}

    for row in field_rows:
        name = row["field"]
        raw = "" if _blank(row.get("Value")) else str(row["Value"]).strip()
        old = (reviewed.get(name) or {}).get("value")
        try:
            value = _parse(kinds[name], raw)
        except ValueError:
            hint = " (use YYYY-MM-DD)" if kinds[name] == "date" else ""
            errors.append(f"{label(name)}: '{raw}' is not a valid {kinds[name]}{hint}")
            continue
        if value != old:
            new[name] = {**(new.get(name) or {}), "value": value, "edited": True, "confidence": "high"}
            edits.append((name, old, value))

    tiers = []
    for t in tier_rows:
        if _blank(t.get("min_qty")) or _blank(t.get("unit_price")):
            continue
        source = t.get("source_quote") if isinstance(t.get("source_quote"), str) else ""
        tier = {"min_qty": int(t["min_qty"]),
                "max_qty": None if _blank(t.get("max_qty")) else int(t["max_qty"]),
                "unit_price": float(t["unit_price"]), "source_quote": source}
        if not source:
            tier["edited"] = True
        tiers.append(tier)
    old_tiers = [{k: t.get(k) for k in _TIER_KEYS} for t in reviewed.get("price_tiers") or []]
    new_tiers = [{k: t.get(k) for k in _TIER_KEYS} for t in tiers]
    if new_tiers != old_tiers:
        new["price_tiers"] = tiers
        edits.append(("price_tiers", old_tiers, new_tiers))

    exceptions = [line.strip() for line in exceptions_text.splitlines() if line.strip()]
    if exceptions != (reviewed.get("supplier_exceptions") or []):
        new["supplier_exceptions"] = exceptions
        edits.append(("supplier_exceptions", reviewed.get("supplier_exceptions"), exceptions))

    return new, edits, errors


def display_value(value) -> str:
    if value is None:
        return ""
    return f"{value:g}" if isinstance(value, float) else str(value)
