# Extraction evaluation report

- Run: 2026-09-15 18:59 UTC · configuration: **recorded extractions**
- Source: recorded (claude) · model(s) `claude-sonnet-5` · prompt `extract_v2`
- Documents: 4 · trials: 1 · runs scored: 4

| Metric | Result | Target |
|---|---|---|
| Field accuracy | 100.0% | ≥ 95% |
| Citations found in document | 100.0% | 100% |
| Exception recall | 100.0% | 100% |
| Exception precision | 94% | ≥ 90% |
| Escalated to stronger model | 0% | |
| Cost per document | $0.0259 ($0.0195–$0.0383) | |
| Latency p50 / p95 (n=4) | 15.0 s / 24.2 s | |
| Prompt cache hit rate | 0% of input tokens | |

> With 4 scored runs, p95 is close to the maximum and a single field is 1.4% of accuracy. Treat small differences as noise.

## Quality gate (escalation trigger)

The gate fires when a citation is missing from the document, a required field has low confidence, no price was found, or a date/country is unreadable after normalization. Below, *wrong* means at least one field disagreed with ground truth.

| | Extraction wrong | Extraction right |
|---|---|---|
| Gate fired | 0 | 0 |
| Gate quiet | 0 | 4 |

- Precision (fired runs that were actually wrong): n/a
- Recall (wrong runs the gate caught): n/a
- False-fire rate (needless escalations): 0% of 4 runs

## Confidence calibration

Self-reported confidence per extracted field, against ground truth.

| Confidence | Fields | Correct |
|---|---|---|
| high | 61 | 100.0% |

## Per-field accuracy

Every field was correct in every trial.

## Misses (last trial)
- **Q4_Sierra_Madre_Castings.xlsx** flags: expected ['MISSING_FIELD', 'PAYMENT_TERMS_BELOW_STANDARD'], found ['MISSING_FIELD', 'PAYMENT_TERMS_BELOW_STANDARD', 'SUPPLIER_EXCEPTIONS']
