# Model routing comparison

Run 2026-09-15 03:05 UTC · 4 labeled documents · prompt `extract_v2`

| Configuration | Field accuracy | Citations | Exception recall | Escalated | Cost / doc | Latency / doc |
|---|---|---|---|---|---|---|
| claude-opus-5 only | 100.0% | 100.0% | 100% | 0% | $0.0427 | 10.1 s |
| claude-sonnet-5 only | 100.0% | 96.7% | 100% | 0% | $0.0247 | 15.2 s |
| routed: claude-sonnet-5 → claude-opus-5 | 100.0% | 100.0% | 100% | 0% | $0.0207 | 11.4 s |

**How to read this:** keep the cheapest configuration whose accuracy and exception recall match the strongest model. With only a handful of documents, a one-field difference is within noise; add labeled quotes before making a production cutover decision.
