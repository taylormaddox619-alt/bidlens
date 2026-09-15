# Model routing comparison

Run 2026-09-15 19:14 UTC · 4 labeled documents × 3 trial(s) = 12 runs per configuration · prompt `extract_v2`

| Configuration | Field accuracy | Citations | Exception recall | Escalated | Cost / doc | p50 | p95 | Cache hit |
|---|---|---|---|---|---|---|---|---|
| claude-opus-5 only | 100.0% | 100.0% | 100.0% | 0% | $0.0306 | 10.0 s | 12.5 s | 84% |
| claude-sonnet-5 only | 100.0% | 100.0% | 100.0% | 0% | $0.0164 | 11.6 s | 15.8 s | 84% |
| claude-sonnet-5 only, effort=low | 100.0% | 100.0% | 100.0% | 0% | $0.0116 | 7.8 s | 10.5 s | 84% |
| claude-sonnet-5 only, effort=medium | 100.0% | 100.0% | 100.0% | 0% | $0.0129 | 8.9 s | 10.7 s | 84% |
| routed: claude-sonnet-5 → claude-opus-5 | 100.0% | 100.0% | 100.0% | 0% | $0.0168 | 11.8 s | 14.6 s | 84% |
| routed: claude-sonnet-5 (effort=low) → claude-opus-5 | 100.0% | 100.0% | 100.0% | 8% | $0.0147 | 7.6 s | 15.9 s | 83% |

Accuracy columns show the mean over trials with the min–max range when trials differ. Latency percentiles are over 12 document runs per configuration.

**How to read this:** keep the cheapest configuration whose accuracy and exception recall match the strongest model across every trial. With a handful of documents, a one-field difference is within noise; add labeled quotes before making a production cutover decision.

## Quality gate per configuration

| Configuration | Gate fired | Fired & wrong | Quiet & wrong | Precision | Recall | False fires |
|---|---|---|---|---|---|---|
| claude-opus-5 only | 0 / 12 | 0 | 0 | n/a | n/a | 0% |
| claude-sonnet-5 only | 0 / 12 | 0 | 0 | n/a | n/a | 0% |
| claude-sonnet-5 only, effort=low | 0 / 12 | 0 | 0 | n/a | n/a | 0% |
| claude-sonnet-5 only, effort=medium | 0 / 12 | 0 | 0 | n/a | n/a | 0% |
| routed: claude-sonnet-5 → claude-opus-5 | 0 / 12 | 0 | 0 | n/a | n/a | 0% |
| routed: claude-sonnet-5 (effort=low) → claude-opus-5 | 1 / 12 | 0 | 0 | 0% | n/a | 8% |

For single-model rows, *gate fired* means the production gate would have escalated that output. For routed rows it means the first pass did escalate, and correctness refers to the final output.

## Confidence calibration (all configurations pooled)

| Confidence | Fields | Correct |
|---|---|---|
| high | 1094 | 100.0% |
| medium | 4 | 100.0% |
