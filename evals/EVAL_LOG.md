# Evaluation log

How the eval harness drove changes. Latest full results: [model_comparison.md](model_comparison.md) and [report.md](report.md).

## 2026-09-15 · Run 1: first live extraction (prompt `extract_v1`, routed Sonnet 5 → Opus 5)

- 3 of 4 quotes escalated to Opus, all for the same reason: `prepayment_percent: citation not found in document`.
- **Root cause (prompt, not model):** v1 said "use 0 when no prepayment is stated" while also requiring a verbatim citation for every value. When a quote says nothing about prepayment, there is nothing to cite, so Sonnet correctly left the citation empty and the quality gate escalated.
- **Fix:** prompt `extract_v2` returns null for unstated prepayment, consistent with every other field. Ground truth updated to match.
- After fix: 0 of 4 escalated; recording cost fell from $0.216 to $0.083 for the four quotes.

## 2026-09-15 · Run 2: model comparison (prompt `extract_v2`)

| Configuration | Field accuracy | Citations | Exception recall | Cost / doc |
|---|---|---|---|---|
| Opus 5 only | 94.4% | 100% | 100% | $0.046 |
| Sonnet 5 only | 91.7% | 96.7% | 93% | $0.023 |
| Routed | 91.7% | 100% | 93% | $0.024 |

- **Misses:** Sonnet copied values as written instead of normalizing them: `15.12.2026` and `August 31, 2026` for dates; `China`, `USA`, `Mexico (USMCA qualifying)` for country of origin.
- **Business impact:** the unparsed date meant **an expired quote was not flagged**. The unrecognized countries caused false "unknown origin" flags and skipped duty estimates. The quality gate didn't catch it, because citations and confidence were fine.
- **Fixes:**
  1. A deterministic normalization layer (`bidlens/normalize.py`) runs after every extraction: dates, country codes, currency codes, Incoterms. It is tested with the exact strings from this run.
  2. The quality gate escalates when a required date or country is still unreadable after normalization.
  3. Prompt v2 now states the normalization rules explicitly; the original wording stays in `source_quote`.

## 2026-09-15 · Run 3: model comparison after fixes

| Configuration | Field accuracy | Citations | Exception recall | Cost / doc |
|---|---|---|---|---|
| Opus 5 only | 100% | 100% | 100% | $0.043 |
| Sonnet 5 only | 100% | 96.7% | 100% | $0.025 |
| **Routed (production)** | **100%** | **100%** | **100%** | **$0.021** |

- **Decision:** keep routing (Sonnet first, Opus on failed checks). It matched Opus on accuracy and exception recall at about half the cost per document.
- **Caveat:** the Sonnet-only and routed runs use the same first-pass model; the citation difference (96.7% vs 100%) is run-to-run variation. This is why citation failures escalate in production, and why every uncited value is also flagged to the buyer.
- **Limitation:** 4 labeled documents is enough to catch systematic problems like the two above, not to certify accuracy. Before production use, expand to 30–50 real historical quotes with repeated trials.

Total API spend for runs 1–3: about $1.10.

## 2026-09-15 · Run 4: cost, latency and routing levers (prompt `extract_v2`, 3 trials)

Goal: replace single-trial numbers with repeated trials and measure the two cost levers the harness had never touched, prompt caching and reasoning effort.

**Probe first (`scripts/probe_api.py`, one document, $0.12).** The prompt file alone is ~600 tokens, below Sonnet's minimum cacheable prefix, but the structured-output schema counts toward the prefix: the second identical call read 2,796 tokens from cache. Effort turned out to be part of the cache key (each effort level wrote its own cache entry). Low effort cut output tokens from 1,380 to 904 and latency from 13.6 s to 8.0 s on that document, with the gate passing.

**Comparison (`--compare --trials 3`, 4 documents × 3 trials × 6 configurations, $1.65).**

| Configuration | Field accuracy | Citations | Recall | Escalated | Cost / doc | p50 | p95 | Cache hit |
|---|---|---|---|---|---|---|---|---|
| Opus 5 only | 100% | 100% | 100% | 0% | $0.0306 | 10.0 s | 12.5 s | 84% |
| Sonnet 5 only | 100% | 100% | 100% | 0% | $0.0164 | 11.6 s | 15.8 s | 84% |
| Sonnet 5, effort=low | 100% | 100% | 100% | 0% | $0.0116 | 7.8 s | 10.5 s | 84% |
| Sonnet 5, effort=medium | 100% | 100% | 100% | 0% | $0.0129 | 8.9 s | 10.7 s | 84% |
| Routed (default effort) | 100% | 100% | 100% | 0% | $0.0168 | 11.8 s | 14.6 s | 84% |
| Routed, first pass effort=low | 100% | 100% | 100% | 8% | $0.0147 | 7.6 s | 15.9 s | 83% |

- **Accuracy did not separate the configurations**: 72 runs, every field correct, so the routing decision is about cost, latency and the safety net.
- **Effort is the larger lever.** Output tokens are ~85% of per-document cost; low effort took Sonnet from $0.0164 to $0.0116 per document (−29%) and p50 from 11.6 s to 7.8 s. Medium sat between.
- **Caching is the second lever**: with 84% of input tokens read from cache, input cost per document is $0.0016 instead of $0.0067, about 31% of the total at low effort. Run 3's "$0.021 per quote" was measured before either lever existed.
- **The gate fired once in 12 low-effort routed runs** (Kessler & Vogt, the bilingual German quote): `payment_terms_days: citation not found in document`. Opus re-extracted it correctly. That one escalation ($0.05, 22 s) is the entire cost difference between Sonnet-low alone and routed-low, and it is the reason routed-low's p95 is the highest in the table. Gate precision on this set is therefore 0 of 1 fired-and-wrong by the strict definition (the final answer was right), and there were no quiet-and-wrong runs in any configuration.
- **Calibration**: 1,094 fields reported `high` confidence and 4 `medium`; all correct. Self-reported confidence carries no signal on this set, which is why the gate is built on citation checks.
- **Harness lesson**: the first attempt at this run crashed after four configurations because the Windows console could not print "→" in a progress line, losing ~$1.20 of calls. The runner now tolerates any console encoding and writes `results_partial.json` after each configuration.

**Decision:** production first pass moves to `claude-sonnet-5` at `effort=low` (`config.EFFORT`), escalation unchanged. Confirmation run of the production configuration (`--trials 3`, $0.14): 100% accuracy, 100% citations, 0 escalations, **$0.0114 per document, p50 7.0 s, p95 9.1 s**. Demo cache re-recorded on the same configuration ($0.05).

**What would reverse it:** any quiet-and-wrong run (a wrong extraction the gate did not catch) at low effort; or escalation rate high enough that routed-low costs more than routed-default (break-even is about 1 escalation in 2 documents).

Total API spend for run 4: about $3.15 including the lost attempt. Cumulative: about $4.25.
