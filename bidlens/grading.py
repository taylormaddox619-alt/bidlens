"""Compare an extracted quote with a known answer key. Shared by the eval harness and AI-generated test quotes."""

from .schemas import FIELD_LABELS, FIELD_SPECS

# Free-text fields are graded leniently: one value must contain the other.
LENIENT = {"supplier_name", "payment_terms", "incoterm_location"}


def values_match(kind: str, expected, actual) -> bool:
    if expected is None or actual is None:
        return expected is None and actual is None
    if kind == "number":
        return abs(float(expected) - float(actual)) <= max(0.005 * abs(float(expected)), 0.01)
    norm = lambda s: " ".join(str(s).lower().replace(".", "").replace(",", "").split())  # noqa: E731
    return norm(expected) == norm(actual)


def compare_fields(truth: dict, extracted: dict, skip: set[str] = frozenset()) -> list[dict]:
    """Field-by-field comparison. `truth` and `extracted` are Quote-shaped dicts."""
    results = []
    for name, _, kind, _ in FIELD_SPECS:
        if name in skip:
            continue
        exp = (truth.get(name) or {}).get("value")
        got = (extracted.get(name) or {}).get("value")
        if name == "prepayment_percent":  # 0 and "not stated" both mean no prepayment
            exp, got = exp or None, got or None
        if name in LENIENT and exp is not None and got is not None:
            ok = str(exp).lower() in str(got).lower() or str(got).lower() in str(exp).lower()
        else:
            ok = values_match(kind, exp, got)
        results.append({"field": name, "label": FIELD_LABELS[name], "expected": exp, "actual": got, "correct": ok})
    if "price_tiers" not in skip:
        exp_tiers = sorted((t["min_qty"], round(t["unit_price"], 2)) for t in truth.get("price_tiers") or [])
        got_tiers = sorted((t["min_qty"], round(t["unit_price"], 2)) for t in extracted.get("price_tiers") or [])
        results.append({"field": "price_tiers", "label": FIELD_LABELS["price_tiers"], "expected": exp_tiers,
                        "actual": got_tiers, "correct": exp_tiers == got_tiers})
    return results
