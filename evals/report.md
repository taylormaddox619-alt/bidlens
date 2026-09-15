# Extraction evaluation report

- Run: 2026-09-15 19:18 UTC · configuration: **routed: claude-sonnet-5 → claude-opus-5**
- Source: live Claude call · model(s) `claude-sonnet-5` · prompt `extract_v2` · effort `low`
- Documents: 4 · trials: 3 · runs scored: 12

| Metric | Result | Target |
|---|---|---|
| Field accuracy | 100.0% | ≥ 95% |
| Citations found in document | 100.0% | 100% |
| Exception recall | 100.0% | 100% |
| Exception precision | 100% | ≥ 90% |
| Escalated to stronger model | 0% | |
| Cost per document | $0.0114 ($0.0096–$0.0139) | |
| Latency p50 / p95 (n=12) | 7.0 s / 9.1 s | |
| Prompt cache hit rate | 84% of input tokens | |

> With 12 scored runs, p95 is close to the maximum and a single field is 0.5% of accuracy. Treat small differences as noise.

## Quality gate (escalation trigger)

The gate fires when a citation is missing from the document, a required field has low confidence, no price was found, or a date/country is unreadable after normalization. Below, *wrong* means at least one field disagreed with ground truth.

| | Extraction wrong | Extraction right |
|---|---|---|
| Gate fired | 0 | 0 |
| Gate quiet | 0 | 12 |

- Precision (fired runs that were actually wrong): n/a
- Recall (wrong runs the gate caught): n/a
- False-fire rate (needless escalations): 0% of 12 runs

## Confidence calibration

Self-reported confidence per extracted field, against ground truth.

| Confidence | Fields | Correct |
|---|---|---|
| high | 183 | 100.0% |

## Per-field accuracy

Every field was correct in every trial.

## Misses (last trial)
None.
