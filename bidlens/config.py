"""Central configuration: paths, model settings, and business assumptions."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SAMPLES_DIR = DATA_DIR / "samples"
REFERENCE_DIR = DATA_DIR / "reference"
GROUND_TRUTH_DIR = DATA_DIR / "ground_truth"
DEMO_CACHE_DIR = DATA_DIR / "demo_cache"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
EVALS_DIR = ROOT / "evals"
DOCS_DIR = ROOT / "docs"
DB_PATH = Path(os.environ.get("BIDLENS_DB", DATA_DIR / "bidlens.duckdb"))

# --- Model routing ---------------------------------------------------------
# Each task runs on the cheapest model expected to hold quality, confirmed with
# `python evals/run_evals.py --compare`. Extraction starts on Sonnet and escalates
# to Opus only when the output fails automatic checks (see extract.escalation_reasons).
# Haiku is deliberately unused: the simple checks are deterministic rules, which
# cost nothing, and the remaining LLM tasks need to read bilingual quotes,
# European number formats, and tier pricing reliably.
MODEL_ROUTES = {
    "extract": os.environ.get("BIDLENS_EXTRACT_MODEL", "claude-sonnet-5"),
    "extract_escalation": os.environ.get("BIDLENS_ESCALATION_MODEL", "claude-opus-5"),
    "memo": os.environ.get("BIDLENS_MEMO_MODEL", "claude-sonnet-5"),
    # Writing test documents from facts that code already chose: formatting work, low effort.
    "generate": os.environ.get("BIDLENS_GENERATE_MODEL", "claude-sonnet-5"),
}
EXTRACT_PROMPT_VERSION = "extract_v2"  # v2: prepayment is null when not stated (v1 said 0, which cannot be cited)
# Reasoning effort per extraction route (low | medium | high). None sends nothing, i.e. the
# model default. Change only on the strength of `python evals/run_evals.py --compare --trials 3`.
# 2026-09-15, 4 docs x 3 trials: low effort matched Opus and default-effort Sonnet at 100% field
# accuracy while cutting cost/doc 13% and p50 latency 36% (evals/EVAL_LOG.md, run 4). The one
# citation slip it produced in 12 runs was caught by the gate and fixed by escalation.
EFFORT = {
    "extract": os.environ.get("BIDLENS_EXTRACT_EFFORT", "low") or None,
    "extract_escalation": os.environ.get("BIDLENS_ESCALATION_EFFORT") or None,  # Opus at its default
}
MEMO_PROMPT_VERSION = "memo_v1"
GENERATE_PROMPT_VERSION = "generate_v1"

# --- AI-generated test quotes ------------------------------------------------
QUOTES_PER_GENERATION = 4
# Visitors without the live passcode share this many generations per UTC day (~$0.15-0.20 each).
PUBLIC_DAILY_GENERATIONS = int(os.environ.get("BIDLENS_PUBLIC_DAILY_GENERATIONS", "10"))
# Server-side refusal fallback: if the model declines, the API re-runs the request
# on Anthropic's recommended fallback model inside the same call. Only sent to
# models documented to accept it.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = {"claude-opus-5", "claude-fable-5-1"}

# USD per 1M tokens (input, output). Used for the cost-per-run metric.
MODEL_PRICING = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
# Prompt caching: the extraction system prompt is sent with cache_control, so repeat calls
# read it from cache. Cache writes cost 1.25x the input price, cache reads 0.1x.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10

# --- Business assumptions (documented on the Governance page) --------------
COST_OF_CAPITAL = 0.08            # annual rate used to value payment-term differences
PRICE_OUTLIER_THRESHOLD = 0.25    # flag unit prices >25% from the peer median
HIGH_TARIFF_THRESHOLD = 0.15      # flag tariff exposure at or above 15%
MANUAL_MINUTES_PER_QUOTE = 25     # baseline: manual read + spreadsheet entry per quote
ASSISTED_MINUTES_PER_QUOTE = 6    # baseline: review + approve an AI extraction
FREIGHT_INCLUDED_INCOTERMS = {"CPT", "CIP", "CFR", "CIF", "DAP", "DPU", "DDP"}
DUTY_INCLUDED_INCOTERMS = {"DDP"}
