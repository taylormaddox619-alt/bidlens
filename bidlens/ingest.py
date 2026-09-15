"""Convert supplier documents (PDF, Excel, text) into plain text for extraction."""

import hashlib
import io
import re
from datetime import date, datetime

import openpyxl
import pdfplumber

SUPPORTED_TYPES = ("pdf", "xlsx", "txt", "csv")


def extract_text(filename: str, data: bytes) -> str:
    ext = filename.rsplit(".", 1)[-1].lower()
    if ext == "pdf":
        return _pdf_text(data)
    if ext == "xlsx":
        return _xlsx_text(data)
    if ext in ("txt", "csv"):
        return data.decode("utf-8", errors="replace")
    raise ValueError(f"Unsupported file type: .{ext}")


def _pdf_text(data: bytes) -> str:
    parts = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            parts.append(f"[Page {i}]\n{page.extract_text() or ''}")
    return "\n".join(parts).strip()


def _fmt_cell(value) -> str:
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float):
        return f"{value:.2f}" if value != int(value) else str(int(value))
    return str(value).strip()


def _xlsx_text(data: bytes) -> str:
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    lines = []
    for ws in wb.worksheets:
        lines.append(f"[Sheet: {ws.title}]")
        for row in ws.iter_rows(values_only=True):
            cells = [_fmt_cell(c) for c in row if c is not None and str(c).strip()]
            if cells:
                lines.append(" | ".join(cells))
    return "\n".join(lines).strip()


def text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize(text: str) -> str:
    """Lowercase, unify punctuation, and collapse whitespace for fuzzy matching."""
    text = text.lower()
    text = text.replace("–", "-").replace("—", "-")
    text = text.replace("‘", "'").replace("’", "'")
    text = text.replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip()


def quote_in_document(source_quote: str, document_text: str) -> bool:
    return normalize(source_quote) in normalize(document_text)
