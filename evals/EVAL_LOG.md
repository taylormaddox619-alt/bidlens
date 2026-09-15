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
