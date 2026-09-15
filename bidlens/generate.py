"""AI-generated test quotes with a hidden answer key.

Division of labour keeps the test honest:
  1. Code randomly chooses each supplier's commercial terms. This is the answer key.
  2. Claude writes a realistic quote document from those terms (layout, language, boilerplate).
  3. The normal extraction pipeline reads the rendered document blind, never seeing the answer key.
  4. The app scores the extraction against the answer key and shows every miss.
"""

import io
import json
import random
import string
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from html import escape

import anthropic
import openpyxl
from pydantic import BaseModel, Field
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from . import config
from .extract import cost_usd, fallback_kwargs, load_prompt
from .ingest import extract_text, normalize
from .reference import fx_table

ITEMS = [  # (item, typical quantity, USD unit price, required lead time weeks)
    ("Air-end housing, cast iron EN-GJL-250, machined", 500, 190, 12),
    ("Centrifugal pump impeller, stainless steel CF8M", 800, 145, 10),
    ("Diaphragm pump manifold, 316L stainless steel", 300, 320, 14),
    ("Motor mount bracket, welded steel, powder coated", 2000, 38, 8),
    ("Ball valve body, forged carbon steel A105", 1200, 64, 10),
    ("Gear shaft, 4140 steel, induction hardened", 1500, 52, 9),
    ("Blower rotor, ductile iron, dynamically balanced", 400, 260, 16),
]

COUNTRIES = {  # legal suffix, cities, currencies, date style, number style, local language, origin wording
    "CN": ("Co., Ltd.", ["Ningbo", "Suzhou", "Qingdao"], ["USD"], "iso", "us", None, "China"),
    "DE": ("GmbH", ["Stuttgart", "Chemnitz", "Dortmund"], ["EUR"], "dotted", "eu", "German", "Germany"),
    "IT": ("S.r.l.", ["Brescia", "Bergamo", "Vicenza"], ["EUR"], "day_month", "eu", "Italian", "Italy"),
    "MX": ("S.A. de C.V.", ["Monterrey", "Saltillo", "Querétaro"], ["USD", "MXN"], "day_month", "us", "Spanish", "Mexico"),
    "IN": ("Pvt. Ltd.", ["Pune", "Coimbatore", "Rajkot"], ["USD", "INR"], "day_month", "us", None, "India"),
    "US": ("Inc.", ["Cleveland, OH", "Milwaukee, WI", "Tulsa, OK"], ["USD"], "month_day", "us", None, "USA"),
    "VN": ("Co., Ltd.", ["Hai Phong", "Binh Duong"], ["USD"], "day_month", "us", None, "Vietnam"),
    "CA": ("Ltd.", ["Hamilton, ON", "Windsor, ON"], ["CAD", "USD"], "iso", "us", None, "Canada"),
}
NAME_A = ["Ironvale", "Bluepeak", "Redstone", "Crescent", "Granite", "Silverline", "Oakridge", "Kestrel",
          "Northgate", "Harborline", "Stonebridge", "Falconer"]
NAME_B = ["Castings", "Precision", "Machining", "Industrial", "Metalworks", "Components", "Foundry", "Engineering"]
EXCEPTIONS = [
    "Prices subject to raw material surcharge if the scrap steel index moves more than 10%.",
    "PPAP documentation is not included and can be quoted separately.",
    "Standard export crates included; custom packaging quoted on request.",
    "Quotation assumes customer drawing revision remains unchanged.",
]
STYLES = {
    "pdf": ["a formal quotation letter with a short line-item table written as text lines",
            "a terse, table-heavy quotation form with label: value lines",
            "an email-style quotation with the terms as a bulleted list"],
    "xlsx": ["a supplier's Excel quotation sheet with a header block, line items, and a terms section"],
}


class GeneratedDocument(BaseModel):
    lines: list[str] = Field(description="Document lines in reading order (PDF styles); empty for spreadsheets")
    table_rows: list[list[str]] = Field(description="Spreadsheet rows of cell strings; empty for PDF styles")


@dataclass
class TestSupplier:
    filename: str
    doc_format: str
    style: str
    language: str | None
    facts: dict
    required_strings: dict  # field -> exact string the document must contain
    truth: dict             # Quote-shaped answer key
    document: bytes = b""
    writer_meta: dict = field(default_factory=dict)
    omitted_fields: list = field(default_factory=list)


# --- Formatting ------------------------------------------------------------------------------
def fmt_money(amount: float, currency: str, style: str) -> str:
    text = f"{amount:,.2f}"
    if style == "eu":
        text = text.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{currency} {text}"


def fmt_date(d: date, style: str) -> str:
    if style == "dotted":
        return d.strftime("%d.%m.%Y")
    if style == "day_month":
        return f"{d.day} {d.strftime('%B %Y')}"
    if style == "month_day":
        return f"{d.strftime('%B')} {d.day}, {d.year}"
    return d.isoformat()


def _t(value):
    # Answer-key values are marked buyer-entered so rules skip citation checks when computing expected flags.
    return {"value": value, "source_quote": None, "confidence": "high", "edited": True}


# --- Step 1: code chooses the facts ---------------------------------------------------------
def random_rfq(rng: random.Random, evaluation_date: date) -> dict:
    item, qty, _, lead = rng.choice(ITEMS)
    quantity = int(round(qty * rng.uniform(0.6, 1.6), -1))
    return {"event_name": f"RFQ-{evaluation_date.year}-{rng.randint(200, 999)} {item.split(',')[0]} (AI-generated)",
            "item": item, "quantity": quantity, "currency": "USD", "required_lead_time_weeks": float(lead),
            "standard_payment_days": 60, "destination": "Charlotte, NC distribution center",
            "evaluation_date": evaluation_date}


def random_supplier(rng: random.Random, rfq: dict, index: int, used_names: set) -> TestSupplier:
    base_usd = next((p for i, _, p, _ in ITEMS if i == rfq["item"]), 100)
    country = rng.choice(list(COUNTRIES))
    suffix, cities, currencies, date_style, num_style, language, origin_label = COUNTRIES[country]
    currency = rng.choice(currencies)
    fx = fx_table()[currency]
    qty = rfq["quantity"]
    eval_date = rfq["evaluation_date"]

    while True:
        name = f"{rng.choice(NAME_A)} {rng.choice(NAME_B)} {suffix}"
        if name not in used_names:
            used_names.add(name)
            break
    city = rng.choice(cities)
    quote_number = "".join(w[0] for w in name.split()[:2]).upper() + f"-{rng.randint(1000, 9999)}"
    money = lambda usd: round(usd / fx, 2)  # noqa: E731  (USD amount -> quote currency)

    unit_price = money(base_usd * rng.uniform(0.8, 1.25))
    tiers = []
    if rng.random() < 0.35 and qty >= 100:
        q1, q2 = int(round(qty * 0.3, -1)) or 10, int(round(qty * 0.8, -1))
        tiers = [{"min_qty": 1, "max_qty": q1 - 1, "unit_price": round(unit_price * 1.08, 2)},
                 {"min_qty": q1, "max_qty": q2 - 1, "unit_price": round(unit_price * 1.03, 2)},
                 {"min_qty": q2, "max_qty": None, "unit_price": unit_price}]
    tooling = money(rng.choice([1500, 2500, 4000, 6000]) * rng.uniform(0.9, 1.1)) if rng.random() < 0.6 else None

    incoterm = rng.choices(["FOB", "EXW", "FCA", "DAP", "DDP", "CIF"], weights=[25, 20, 15, 20, 10, 10])[0]
    if country == "US" and incoterm in ("FOB", "CIF"):
        incoterm = "FCA"
    location = "Charlotte, NC" if incoterm in ("DAP", "DDP") else ("Charleston, SC" if incoterm == "CIF" else city)
    freight = None
    if incoterm in config.FREIGHT_INCLUDED_INCOTERMS:
        freight_text = f"Freight included ({incoterm} {location})"
    elif rng.random() < 0.5:
        freight = money(qty * rng.uniform(4, 14))
        freight_text = f"Freight to Charlotte, NC (total): {fmt_money(freight, currency, num_style)}"
    else:
        freight_text = "Freight not included; to be arranged by the buyer"

    weeks = max(4, int(rfq["required_lead_time_weeks"]) + rng.randint(-5, 6))
    lead_text = f"{weeks * 7} days after receipt of order" if rng.random() < 0.3 else f"{weeks} weeks ARO"
    days = rng.choice([30, 45, 60, 60, 90])
    prepay = rng.choice([20, 30, 50]) if rng.random() < 0.2 else None
    payment_text = f"{prepay}% deposit with order, balance net {days} days" if prepay else f"Net {days} days"
    warranty = rng.choice([12, 12, 18, 24, None])
    moq = int(round(qty * rng.choice([0.25, 0.5, 0.5, 1.2]), -1)) if rng.random() < 0.8 else None

    quote_date = eval_date - timedelta(days=rng.randint(3, 40))
    valid_until = (eval_date - timedelta(days=rng.randint(1, 10)) if rng.random() < 0.2
                   else quote_date + timedelta(days=rng.choice([45, 60, 90])))
    exceptions = rng.sample(EXCEPTIONS, k=rng.choice([0, 1, 1, 2]))

    facts = {
        "supplier_name": name, "supplier_city": city, "quote_number": quote_number,
        "quote_date": fmt_date(quote_date, date_style), "valid_until": fmt_date(valid_until, date_style),
        "customer": "Procurement Excellence, Charlotte, NC", "item": rfq["item"], "quantity": f"{qty:,}",
        "currency": currency,
        "unit_price": None if tiers else fmt_money(unit_price, currency, num_style),
        "price_breaks": [f"{t['min_qty']:,}{'+' if t['max_qty'] is None else '-' + format(t['max_qty'], ',')} pcs: "
                         f"{fmt_money(t['unit_price'], currency, num_style)} per piece" for t in tiers] or None,
        "tooling_one_time": fmt_money(tooling, currency, num_style) if tooling else None,
        "incoterms": f"{incoterm} {location} (Incoterms 2020)", "freight": freight_text,
        "country_of_origin": f"Made in {origin_label}", "lead_time": lead_text, "payment_terms": payment_text,
        "warranty": f"{warranty} months from delivery" if warranty else None,
        "minimum_order_quantity": f"{moq:,} pcs" if moq else None, "notes": exceptions or None,
    }
    facts = {k: v for k, v in facts.items() if v is not None}

    # Exact strings the written document must contain, keyed by the answer-key field they prove.
    required = {"supplier_name": name, "quote_number": quote_number, "quote_date": facts["quote_date"],
                "valid_until": facts["valid_until"], "lead_time_weeks": lead_text,
                "payment_terms_days": payment_text, "unit_price": fmt_money(unit_price, currency, num_style)}
    for key, fact in (("tooling_cost", "tooling_one_time"), ("warranty_months", "warranty"),
                      ("moq", "minimum_order_quantity")):
        if fact in facts:
            required[key] = facts[fact]
    if freight is not None:
        required["freight_cost"] = freight_text.split(": ", 1)[1]

    truth = {
        "supplier_name": _t(name), "quote_number": _t(quote_number), "quote_date": _t(quote_date.isoformat()),
        "valid_until": _t(valid_until.isoformat()), "currency": _t(currency), "unit_price": _t(unit_price),
        "price_tiers": [{**t, "source_quote": "", "edited": True} for t in tiers],
        "tooling_cost": _t(tooling), "freight_cost": _t(freight), "incoterm": _t(incoterm),
        "incoterm_location": _t(location.split(",")[0]), "country_of_origin": _t(country),
        "lead_time_weeks": _t(float(weeks)), "payment_terms": _t(payment_text),
        "payment_terms_days": _t(float(days)), "prepayment_percent": _t(float(prepay) if prepay else None),
        "warranty_months": _t(float(warranty) if warranty else None), "moq": _t(float(moq) if moq else None),
        "supplier_exceptions": exceptions,
    }
    doc_format = "xlsx" if rng.random() < 0.25 else "pdf"
    style = rng.choice(STYLES[doc_format])
    if language and doc_format == "pdf" and rng.random() < 0.5:
        style += f", bilingual {language}/English labels"
    slug = "".join(c for c in name.split()[0] if c in string.ascii_letters)
    return TestSupplier(filename=f"AI{index}_{slug}_{country}.{doc_format}", doc_format=doc_format, style=style,
                        language=language, facts=facts, required_strings=required, truth=truth)


# --- Step 2: Claude writes the document -----------------------------------------------------------
def missing_strings(text: str, required: dict) -> list[str]:
    haystack = normalize(text)
    return [f for f, s in required.items() if normalize(s) not in haystack]


def write_document(client: anthropic.Anthropic, supplier: TestSupplier) -> None:
    """Fill supplier.document (rendered bytes). Retries once if the writer drops a fact."""
    model = config.MODEL_ROUTES["generate"]
    meta = {"model": model, "prompt_version": config.GENERATE_PROMPT_VERSION, "input_tokens": 0,
            "output_tokens": 0, "cost_usd": 0.0, "status": "ok", "source": "live", "purpose": "generate"}
    start = time.perf_counter()
    message = (f"Document style: {supplier.style}.\nFormat: {'spreadsheet' if supplier.doc_format == 'xlsx' else 'PDF'}.\n\n"
               f"Facts (copy value strings exactly):\n{json.dumps(supplier.facts, indent=2, ensure_ascii=False)}")
    missing = list(supplier.required_strings)
    for attempt in (1, 2):
        response = client.messages.parse(
            model=model, max_tokens=8000, system=load_prompt(config.GENERATE_PROMPT_VERSION),
            messages=[{"role": "user", "content": message}], output_format=GeneratedDocument,
            output_config={"effort": "low"}, **fallback_kwargs(model),
        )
        meta["input_tokens"] += response.usage.input_tokens
        meta["output_tokens"] += response.usage.output_tokens
        meta["cost_usd"] += cost_usd(response.model, response.usage.input_tokens, response.usage.output_tokens)
        doc = response.parsed_output
        if doc is None:
            continue
        rendered = render(supplier, doc)
        missing = missing_strings(extract_text(supplier.filename, rendered), supplier.required_strings)
        supplier.document = rendered
        if not missing:
            break
        message += ("\n\nYour previous draft left out these facts or changed their wording: "
                    f"{', '.join(supplier.required_strings[m] for m in missing)}. Include each exactly.")
    supplier.omitted_fields = missing
    meta.update(latency_s=time.perf_counter() - start, attempts=attempt)
    if not supplier.document:
        meta.update(status="failed", error="Writer returned no document")
    supplier.writer_meta = meta


def render(supplier: TestSupplier, doc: GeneratedDocument) -> bytes:
    buffer = io.BytesIO()
    if supplier.doc_format == "xlsx":
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Quotation"
        rows = doc.table_rows or [[line] for line in doc.lines]
        for row in rows:
            ws.append([str(c) for c in row])
        wb.save(buffer)
    else:
        styles = getSampleStyleSheet()
        lines = doc.lines or [" | ".join(r) for r in doc.table_rows]
        story = []
        for i, line in enumerate(lines):
            if not line.strip():
                story.append(Spacer(1, 6))
                continue
            story.append(Paragraph(escape(line), styles["Heading2"] if i == 0 else styles["BodyText"]))
        SimpleDocTemplate(buffer, pagesize=letter, leftMargin=0.8 * inch, rightMargin=0.8 * inch).build(story)
    return buffer.getvalue()


# --- Orchestration ----------------------------------------------------------------------------------
def build_test_quotes(api_key: str, rfq: dict, count: int = config.QUOTES_PER_GENERATION,
                      seed: int | None = None) -> list[TestSupplier]:
    """Choose facts with code, then have Claude write each document (in parallel)."""
    from concurrent.futures import ThreadPoolExecutor

    rng = random.Random(seed)
    used: set = set()
    suppliers = [random_supplier(rng, rfq, i + 1, used) for i in range(count)]
    client = anthropic.Anthropic(api_key=api_key)

    def safe_write(supplier: TestSupplier) -> None:
        try:
            write_document(client, supplier)
        except Exception as e:  # one failed document shouldn't sink the whole scenario
            supplier.writer_meta = {"status": "failed", "error": str(e), "model": config.MODEL_ROUTES["generate"],
                                    "purpose": "generate", "source": "live", "cost_usd": 0.0}

    with ThreadPoolExecutor(max_workers=count) as pool:
        list(pool.map(safe_write, suppliers))
    return suppliers
