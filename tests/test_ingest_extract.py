import json

from bidlens import config
from bidlens.extract import cost_usd, demo_extraction
from bidlens.ingest import extract_text, quote_in_document
from bidlens.schemas import Quote


def test_excel_ingest_keeps_rows(samples):
    text = samples["sierra"]["text"]
    assert "[Sheet: Cotizacion]" in text
    assert "500 | 194" in text


def test_quote_matching_ignores_case_and_whitespace():
    assert quote_in_document("net   60", "Terms: NET 60\n")
    assert not quote_in_document("Net 90", "Terms: Net 60")


def test_unsupported_file_type():
    try:
        extract_text("quote.docx", b"")
    except ValueError as e:
        assert "Unsupported" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_demo_cache_covers_every_sample_and_validates(samples):
    for s in samples.values():
        quote, meta = demo_extraction(s["text"])
        assert quote is not None, s["filename"]
        Quote.model_validate(quote)
        assert meta["source"] == "demo_cache"


def test_demo_mode_rejects_unknown_documents():
    quote, meta = demo_extraction("some other supplier's quote")
    assert quote is None and meta["status"] == "not_cached"


def test_cost_calculation():
    price_in, price_out = config.MODEL_PRICING["claude-opus-5"]
    assert cost_usd("claude-opus-5", 1_000_000, 0) == price_in
    assert cost_usd("claude-opus-5", 0, 1_000_000) == price_out


def test_quote_schema_is_strict_json_schema():
    schema = Quote.model_json_schema()
    assert "unit_price" in schema["required"]
    json.dumps(schema)


def test_schema_and_prompt_agree_on_unstated_prepayment():
    """extract_v2 says null when no prepayment is stated (0 cannot be cited). The field description is sent to
    the model with every call as part of the output schema, so it must not say the opposite."""
    description = Quote.model_json_schema()["properties"]["prepayment_percent"]["description"]
    assert "null" in description and "0 if none" not in description
