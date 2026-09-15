"""LLM extraction: supplier document text -> validated Quote with citations.

Two sources:
  * live  - calls Claude with a schema-constrained output (structured outputs)
  * demo  - replays a recorded extraction for known sample documents (no API key, no cost)
"""

import json
import time

import anthropic
from pydantic import ValidationError

from . import config
from .ingest import text_sha
from .schemas import RFQ, Quote


def load_prompt(version: str) -> str:
    return (config.PROMPTS_DIR / f"{version}.md").read_text(encoding="utf-8")


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = config.MODEL_PRICING.get(model, config.MODEL_PRICING[config.DEFAULT_MODEL])
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


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


def live_extraction(text: str, rfq: RFQ, api_key: str, model: str = config.DEFAULT_MODEL,
                    prompt_version: str = config.EXTRACT_PROMPT_VERSION) -> tuple[dict | None, dict]:
    """Call Claude. Returns (quote_dict or None, run metadata). Retries once on schema failure."""
    client = anthropic.Anthropic(api_key=api_key)
    system = load_prompt(prompt_version)
    meta = {"source": "live", "model": model, "prompt_version": prompt_version,
            "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_s": 0.0}
    start = time.perf_counter()
    last_error = None

    for attempt in (1, 2):
        try:
            response = client.beta.messages.parse(
                model=model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": _user_message(text, rfq)}],
                output_format=Quote,
                betas=[config.FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.AuthenticationError:
            last_error = "Invalid Anthropic API key."
            break
        except anthropic.RateLimitError:
            last_error = "Rate limited by the Anthropic API - try again shortly."
            break
        except anthropic.APIStatusError as e:
            last_error = f"API error {e.status_code}: {e.message}"
            break
        except anthropic.APIConnectionError:
            last_error = "Could not reach the Anthropic API."
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

    meta.update(status="failed", error=last_error, latency_s=time.perf_counter() - start)
    return None, meta


def extract(text: str, rfq: RFQ, mode: str, api_key: str | None = None,
            model: str = config.DEFAULT_MODEL) -> tuple[dict | None, dict]:
    if mode == "live":
        if not api_key:
            return None, {"source": "live", "status": "failed", "error": "No API key configured."}
        return live_extraction(text, rfq, api_key, model)
    return demo_extraction(text)
