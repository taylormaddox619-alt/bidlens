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

# --- Model settings -------------------------------------------------------
DEFAULT_MODEL = os.environ.get("BIDLENS_MODEL", "claude-opus-5")
EXTRACT_PROMPT_VERSION = "extract_v1"
MEMO_PROMPT_VERSION = "memo_v1"
# Server-side refusal fallback: if the primary model declines, the API re-runs
# the request on Anthropic's recommended fallback model inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# USD per 1M tokens (input, output). Used for the cost-per-run metric.
MODEL_PRICING = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

# --- Business assumptions (documented on the Governance page) --------------
COST_OF_CAPITAL = 0.08            # annual rate used to value payment-term differences
PRICE_OUTLIER_THRESHOLD = 0.25    # flag unit prices >25% from the peer median
HIGH_TARIFF_THRESHOLD = 0.15      # flag tariff exposure at or above 15%
MANUAL_MINUTES_PER_QUOTE = 25     # baseline: manual read + spreadsheet entry per quote
ASSISTED_MINUTES_PER_QUOTE = 6    # baseline: review + approve an AI extraction
FREIGHT_INCLUDED_INCOTERMS = {"CPT", "CIP", "CFR", "CIF", "DAP", "DPU", "DDP"}
DUTY_INCLUDED_INCOTERMS = {"DDP"}
