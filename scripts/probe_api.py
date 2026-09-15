"""Probe the API levers before an eval run: does the prompt cache, and what does effort do?

Usage:
  python scripts/probe_api.py            # ~$0.15: 2x Sonnet cached, Sonnet at low/medium effort, 1x Opus
  python scripts/probe_api.py --doc Q3_Lakeshore_Cast.pdf

Prints cache write/read tokens, output tokens, latency, cost and whether the quality gate would
fire, for one sample document. Findings go into evals/EVAL_LOG.md.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bidlens import config  # noqa: E402
from bidlens.credentials import get_secret  # noqa: E402
from bidlens.extract import escalation_reasons, live_extraction  # noqa: E402
from bidlens.ingest import extract_text  # noqa: E402
from bidlens.schemas import RFQ  # noqa: E402


def run(label: str, text: str, rfq: RFQ, api_key: str, model: str, effort: str | None = None) -> None:
    quote, meta = live_extraction(text, rfq, api_key, model, effort=effort)
    gate = escalation_reasons(quote, meta, text) if quote else [meta.get("error")]
    print(f"{label:34s} in {meta['input_tokens']:5d}  cache-write {meta['cache_write_tokens']:5d}  "
          f"cache-read {meta['cache_read_tokens']:5d}  out {meta['output_tokens']:5d}  "
          f"{meta['latency_s']:5.1f}s  ${meta['cost_usd']:.4f}  gate: {'; '.join(gate) or 'pass'}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--doc", default="Q1_Jadeport_Foundry.pdf")
    args = parser.parse_args()
    api_key = get_secret("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("No API key found (environment or .streamlit/secrets.toml).")
    rfq = RFQ.model_validate(json.loads((config.SAMPLES_DIR / "demo_rfq.json").read_text(encoding="utf-8")))
    text = extract_text(args.doc, (config.SAMPLES_DIR / args.doc).read_bytes())
    cheap, strong = config.MODEL_ROUTES["extract"], config.MODEL_ROUTES["extract_escalation"]
    print(f"document {args.doc} · prompt {config.EXTRACT_PROMPT_VERSION}\n")
    run(f"{cheap} (call 1)", text, rfq, api_key, cheap)
    run(f"{cheap} (call 2, expect cache read)", text, rfq, api_key, cheap)
    run(f"{cheap} effort=medium", text, rfq, api_key, cheap, "medium")
    run(f"{cheap} effort=low", text, rfq, api_key, cheap, "low")
    run(f"{strong} (call 1)", text, rfq, api_key, strong)
    print("\nIf cache-write and cache-read are both 0, the prompt is below the model's minimum cacheable prefix.")


if __name__ == "__main__":
    main()
