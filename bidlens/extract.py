"""LLM extraction: supplier document text -> validated Quote with citations.

Two sources:
  * live  - calls Claude with a schema-constrained output (structured outputs)
  * demo  - replays a recorded extraction for known sample documents (no API key, no cost)

Live extraction is routed: a cheaper first-pass model handles every document, and
the result escalates to a stronger model only when it fails automatic quality checks.
"""

import json
import time

import anthropic
from pydantic import ValidationError

from . import config
from .ingest import quote_in_document, text_sha
from .schemas import FIELD_SPECS, RFQ, Quote

REQUIRED_FIELDS = {name for name, _, _, required in FIELD_SPECS if required}


def load_prompt(version: str) -> str:
    return (config.PROMPTS_DIR / f"{version}.md").read_text(encoding="utf-8")


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    # Unknown models are priced at the most expensive routed tier so spend is never understated.
    price_in, price_out = config.MODEL_PRICING.get(model, max(config.MODEL_PRICING.values()))
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def fallback_kwargs(model: str) -> dict:
    if model in config.FALLBACK_MODELS:
        return {"betas": [config.FALLBACK_BETA], "fallbacks": "default"}
    return {}


def _user_message(text: str, rfq: RFQ) -> str:
    return (
        f"RFQ context:\n- Requested quantity: {rfq.quantity}\n- RFQ currency: {rfq.currency}\n"
        f"- Item: {rfq.item}\n\n<supplier_document>\n{text}\n</supplier_document>\n\n"
        "Extract the quote terms from the supplier document."
    )


def demo_extraction(text: str) -> tuple[dict | None, dict]:
    path = config.DEMO_CACHE_DIR / f"{text_sha(text)}.json"
    if not path.exists():
        return None, {"source": "demo", "status": "not_cached",
                      "error": "Demo mode only supports the bundled sample quotes. Switch to Live mode for other files."}
    record = json.loads(path.read_text(encoding="utf-8"))
    meta = dict(record["meta"])
    meta["recorded_source"] = meta.get("source")
    meta["source"] = "demo_cache"
    return record["quote"], meta


def live_extraction(text: str, rfq: RFQ, api_key: str, model: str,
                    prompt_version: str = config.EXTRACT_PROMPT_VERSION) -> tuple[dict | None, dict]:
    """One model, one retry on schema failure. Returns (quote_dict or None, run metadata).

    `error_kind` is "capability" when a stronger model might succeed (bad schema, truncation,
    refusal) and "infra" when it would not help (auth, rate limit, network).
    """
    client = anthropic.Anthropic(api_key=api_key)
    system = load_prompt(prompt_version)
    meta = {"source": "live", "model": model, "prompt_version": prompt_version,
            "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_s": 0.0}
    start = time.perf_counter()
    last_error, error_kind = None, "capability"

    for attempt in (1, 2):
        try:
            response = client.beta.messages.parse(
                model=model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": _user_message(text, rfq)}],
                output_format=Quote,
                **fallback_kwargs(model),
            )
        except anthropic.AuthenticationError:
            last_error, error_kind = "Invalid Anthropic API key.", "infra"
            break
        except anthropic.RateLimitError:
            last_error, error_kind = "Rate limited by the Anthropic API - try again shortly.", "infra"
            break
        except anthropic.APIStatusError as e:
            last_error, error_kind = f"API error {e.status_code}: {e.message}", "infra"
            break
        except anthropic.APIConnectionError:
            last_error, error_kind = "Could not reach the Anthropic API.", "infra"
            break
        except ValidationError as e:
            last_error = f"Output failed schema validation (attempt {attempt}): {e.error_count()} errors"
            continue

        meta["input_tokens"] += response.usage.input_tokens
        meta["output_tokens"] += response.usage.output_tokens
        meta["cost_usd"] += cost_usd(response.model, response.usage.input_tokens, response.usage.output_tokens)
        meta["model"] = response.model
        meta["request_id"] = response._request_id

        if response.stop_reason == "refusal":
            last_error = "Model declined to process this document."
            break
        if response.stop_reason == "max_tokens":
            last_error = f"Output truncated (attempt {attempt})."
            continue
        if response.parsed_output is None:
            last_error = f"No structured output returned (attempt {attempt})."
            continue

        meta.update(status="ok", latency_s=time.perf_counter() - start, attempts=attempt)
        return response.parsed_output.model_dump(), meta

    meta.update(status="failed", error=last_error, error_kind=error_kind, latency_s=time.perf_counter() - start)
    return None, meta


def escalation_reasons(quote: dict | None, meta: dict, text: str) -> list[str]:
    """Automatic quality gate for a first-pass extraction. Empty list = accept."""
    if quote is None:
        return [f"first pass failed: {meta.get('error')}"]
    reasons = []
    for name, *_ in FIELD_SPECS:
        field = quote.get(name) or {}
        if field.get("value") in (None, ""):
            continue
        if not field.get("source_quote") or not quote_in_document(field["source_quote"], text):
            reasons.append(f"{name}: citation not found in document")
        elif name in REQUIRED_FIELDS and field.get("confidence") == "low":
            reasons.append(f"{name}: low confidence on a required field")
    for tier in quote.get("price_tiers") or []:
        if not quote_in_document(tier.get("source_quote") or "", text):
            reasons.append(f"price tier from {tier.get('min_qty')}: citation not found in document")
    if not (quote.get("unit_price") or {}).get("value") and not quote.get("price_tiers"):
        reasons.append("no price extracted")
    return reasons


def routed_extraction(text: str, rfq: RFQ, api_key: str,
                      prompt_version: str = config.EXTRACT_PROMPT_VERSION,
                      routes: dict | None = None) -> tuple[dict | None, dict]:
    """First pass on the cheaper model; escalate to the stronger model only if the gate fails."""
    routes = routes or config.MODEL_ROUTES
    quote, first = live_extraction(text, rfq, api_key, routes["extract"], prompt_version)
    first["purpose"] = "extract"
    reasons = escalation_reasons(quote, first, text)

    if not reasons or first.get("error_kind") == "infra" or routes["extract_escalation"] == routes["extract"]:
        return quote, {**first, "escalated": False, "runs": [first]}

    better, second = live_extraction(text, rfq, api_key, routes["extract_escalation"], prompt_version)
    second["purpose"] = "extract_escalation"
    runs = [first, second]
    totals = {k: sum(r.get(k, 0) for r in runs) for k in ("input_tokens", "output_tokens", "cost_usd", "latency_s")}
    meta = {"source": "live", "prompt_version": prompt_version, **totals, "runs": runs,
            "escalated": True, "escalation_reasons": reasons, "first_pass_model": first["model"]}

    if better is not None:
        return better, {**meta, "model": second["model"], "status": "ok"}
    if quote is not None:
        # Escalation itself failed; keep the first pass. The rules engine still flags its weak fields.
        return quote, {**meta, "model": first["model"], "status": "ok",
                       "escalation_error": second.get("error")}
    return None, {**meta, "model": second["model"], "status": "failed", "error": second.get("error")}


def extract(text: str, rfq: RFQ, mode: str, api_key: str | None = None) -> tuple[dict | None, dict]:
    if mode == "live":
        if not api_key:
            return None, {"source": "live", "status": "failed", "error": "No API key configured."}
        return routed_extraction(text, rfq, api_key)
    return demo_extraction(text)
