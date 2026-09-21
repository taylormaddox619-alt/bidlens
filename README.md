# BidLens: AI-Assisted Supplier Bid Analysis

**BidLens reads supplier quotes with Claude, flags commercial risks, and compares bids on total landed cost. A buyer approves every step.**

> 🔗 **Live demo:** https://bidlens-4mwcnxw6tc2btynjwfu99u.streamlit.app/ · fictional data, no login needed

Buyers compare supplier quotes that arrive as PDFs and spreadsheets in every format imaginable. They re-key the data into Excel, and they often compare on **unit price**, which misses freight, tariffs, tooling and payment terms. BidLens automates the repetitive steps and leaves judgment calls to the buyer.

| Step | What happens | Who decides |
|---|---|---|
| **Extract** | Claude turns each quote into a fixed schema; **every value cites the exact source text**. Dates, countries, currencies and Incoterms are then normalized in code | AI + code |
| **Check** | 16 deterministic rules flag risks, each graded 🔴 high / 🟠 medium / 🔵 info with a recommended action | Rules |
| **Review** | Buyer works through quotes highest-risk first, verifies highlighted evidence, corrects values, and approves or rejects. **Comparison is locked until every quote is reviewed.** | **Buyer** |
| **Compare** | Normalized landed cost (FX, tier pricing, tooling, freight, duty, payment-term cost of capital), weighted scoring with visible rationale, red flags by supplier | Assisted |
| **Decide** | Draft memo from approved data only; overriding the top-scored bid requires a justification; full audit log | **Buyer** |
| **Measure** | AI Scorecard tracks adoption, quality (edit rate, eval accuracy), hours saved, cost avoided, API spend by model | Leadership |

## How to use the demo

A step tracker at the top of every page shows where you are. The blue **Next** button always names the next action.

1. **New Bid Event → Quick start.** Click **Load demo scenario**, or **Generate a fresh scenario** for suppliers nobody has seen. The quotes table lists the riskiest quotes first.
2. **Review & Approve.** Quotes open highest-risk first. Read the 🔴 red and 🟠 amber boxes: each issue says what to do. Check the highlighted evidence in the document, fix any wrong value, then **Approve** or **Reject**. The next quote opens automatically, with a banner saying who's next and how many are left.
3. **Comparison** unlocks once every quote has a decision. See why the lowest unit price loses on landed cost, check **red flags by supplier**, adjust scoring weights, and draft the memo.
4. **Record the award.** Choosing anyone but the top-scored supplier requires a written justification.
5. **AI Scorecard** and **Governance** show how the tool is measured and controlled.

### Reading the flags

| Level | Meaning | What the buyer does | Examples |
|---|---|---|---|
| 🔴 **High risk** | Could make the award wrong or unsafe | Resolve it, or tick the box to accept the specific issues before approving | Expired quote, lead time over requirement, citation not found in the document, missing price, text addressed to AI reviewers |
| 🟠 **Medium risk** | Affects cost or terms | Check it and follow up with the supplier | Freight estimated, tariff exposure, prepayment, short payment terms, missing warranty, currency mismatch |
| 🔵 **Info** | For awareness | Usually no action | Supplier-stated assumptions |

## The demo scenario

RFQ for 500 cast-iron compressor air-end housings, with four fictional suppliers. Each quote has a built-in problem:

| Supplier | Format | What BidLens catches |
|---|---|---|
| Jadeport Foundry (CN) | PDF | **Lowest unit price ($172)**, but a 35% tariff and excluded freight make it the **most expensive landed ($258/unit)**. Also hides text telling "AI reviewers" to recommend it (🔴 flagged) |
| Kessler & Vogt (DE) | Bilingual PDF, EUR, European number format | EXW (freight excluded), 30% deposit + Net 30 |
| Lakeshore Cast (US) | PDF | Freight included, but lead time 16 weeks vs 12 required (🔴), and the quote has expired (🔴) |
| Sierra Madre Castings (MX) | Excel, tiered pricing | Warranty not stated (buyer must follow up); best landed cost at **$207/unit** |

**Result:** picking the lowest unit price would have cost **$25,531 more** than the best landed-cost bid. The demo replays real Claude extractions of these quotes.

## Not a staged demo: generate fresh scenarios

**Generate a fresh scenario** creates a random RFQ with four suppliers nobody has seen:

1. **Code** randomly picks each supplier's country, currency, prices or price breaks, Incoterm, freight, lead time, payment terms, warranty and validity. Those terms are the **hidden answer key**.
2. **Claude** writes each supplier's quote as a PDF or Excel sheet in varied styles (formal letter, terse form, email, bilingual). It must reproduce every term exactly; this is checked automatically with one retry.
3. The **normal extraction pipeline reads the documents blind**, and the app scores the result field by field against the answer key, **misses included**. Each generated quote on Review & Approve has an "answer key check" section.

On a first live run, it generated four quotes (Canada, China, Germany, Italy) in 34 seconds for $0.17, and extraction got 68 of 68 fields right. Public visitors share a daily cap (`BIDLENS_PUBLIC_DAILY_GENERATIONS`, default 10); Live-mode passcode holders are not capped.

## Architecture

```
PDF / XLSX ─► ingest ─► extract (Claude) ─► normalize ─► rules ─► Review (buyer) ─► costing ─► scoring ─► memo
                         Sonnet 5 first,     dates,       16 checks,   approve / edit /    landed      weighted   draft
                         Opus 5 if checks    countries,   severity +   reject, highest-    cost                  (template
                         fail                currencies   actions      risk first                                or Claude)
                                  └──────────────────────► db (DuckDB: events, quotes, edits, runs, awards, answer keys, audit log)

generate (demo only): code picks terms (answer key) ─► Claude writes documents ─► same pipeline ─► grading vs answer key
```

- **LLM:** Anthropic Claude via schema-constrained structured outputs (Pydantic). Prompts are versioned files in `bidlens/prompts/` (`extract_v2`, `memo_v1`, `generate_v1`).
- **Cost-aware model routing:** extraction runs on `claude-sonnet-5` at low reasoning effort and escalates to `claude-opus-5` only when the output fails automatic checks: a citation not found in the document, low confidence on a required field, no price, an unreadable date or country, an unrecognized currency or Incoterm, or a schema failure. The system prompt and output schema are prompt-cached (84% of input tokens served from cache). Memos and test-quote writing use Sonnet at lower effort. Rules, costing and scoring use no LLM. See [Cost, latency and routing](#cost-latency-and-routing).
- **Guardrails:**
  - verbatim citations checked against the source, and null-not-guess instructions;
  - deterministic normalization, plus untrusted-document framing and a prompt-injection detector;
  - severity-graded flags with recommended actions, and explicit acceptance of high-risk issues;
  - a locked comparison and a DRAFT-labelled memo.
- **Data:** DuckDB with all SQL isolated in `bidlens/db.py`, so it can move to Snowflake.
- **Deploys:** Streamlit multipage app. `reload_guard.py` reloads redeployed modules so a push can't leave stale code running.
- **Quality:** pytest suite covering rules, recommended actions, costing, scoring, review edits, normalization, routing, the generator, UI guidance logic and the reload guard, plus an evaluation harness against labelled ground truth. [EVAL_LOG.md](evals/EVAL_LOG.md) shows how eval runs found two real defects (a prompt contradiction, and an unparsed expiry date that hid an expired quote) and how they were fixed.

## Cost, latency and routing

Routing is a measured decision, not a default. Every configuration below ran the 4 labelled quotes 3 times (12 runs each, 2026-09-15, prompt `extract_v2`). Full tables, the gate 2×2 and confidence calibration are in [model_comparison.md](evals/model_comparison.md); every run is appended to [history.jsonl](evals/history.jsonl) and charted on the AI Scorecard page.

| Configuration | Field accuracy | Exception recall | Escalated | Cost / doc | Latency p50 | p95 |
|---|---|---|---|---|---|---|
| Opus 5 only | 100% | 100% | 0% | $0.0306 | 10.0 s | 12.5 s |
| Sonnet 5 only (default effort) | 100% | 100% | 0% | $0.0164 | 11.6 s | 15.8 s |
| Sonnet 5 only, effort=medium | 100% | 100% | 0% | $0.0129 | 8.9 s | 10.7 s |
| Sonnet 5 only, effort=low | 100% | 100% | 0% | $0.0116 | 7.8 s | 10.5 s |
| Routed, default effort | 100% | 100% | 0% | $0.0168 | 11.8 s | 14.6 s |
| **Routed, effort=low (production)** | **100%** | **100%** | 8% | **$0.0147** | **7.6 s** | 15.9 s |

Accuracy was identical in all 72 runs, so the decision comes down to cost, latency and safety:

- **Reasoning effort is the biggest lever.** Output tokens are ~85% of per-document cost. Low effort cut Sonnet's output by a third: cost/doc −29%, p50 latency −33% versus default effort, with no accuracy loss. Production now runs the first pass at `effort=low` (`config.EFFORT`).
- **Prompt caching is the second lever.** The system prompt plus the structured-output schema form a ~2,800-token stable prefix, so 84% of input tokens are served from cache at 0.1× the input price. Per document that is $0.0016 instead of $0.0067 of input, about 31% off the total. Cache write/read tokens are logged on every run and priced separately, so the scorecard's spend is exact. Note that effort is part of the cache key: changing it invalidates the prefix.
- **The escalation gate earns its keep at low effort.** In 12 routed runs it fired once, on the bilingual German quote, because the payment-terms citation was not verbatim; Opus re-extracted it correctly. That single escalation costs $0.05 and 22 s, which is why routed low-effort averages $0.0147 rather than $0.0116 and why its p95 is the highest in the table. It is still cheaper and faster at the median than routing at default effort, and it keeps the safety net.
- **Confidence is honest but uninformative here.** Across 1,098 extracted fields the model reported `high` on all but 4, and every one was correct, so the gate relies on citation checks rather than self-reported confidence.

**What would change the decision:** a wrong extraction that the gate misses (quiet & wrong > 0) would move the first pass back to default effort; Haiku is only worth testing once the labelled set reaches 30+ real quotes; for offline batch re-extraction the Batches API would halve cost again at the price of latency, and is not built. The production run after the change (`evals/report.md`): **$0.0114 per document, p50 7.0 s, p95 9.1 s, 0 escalations, 100% accuracy over 12 runs.**

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run Home.py
```

Demo mode works without an API key. To enable Live mode and AI-generated scenarios, create `.streamlit/secrets.toml` (gitignored). The app, scripts and evals all read it:

```toml
ANTHROPIC_API_KEY = "sk-ant-..."
BIDLENS_LIVE_PASSCODE = "choose-a-passcode"   # gates Live mode and uncapped generation on a public deployment
```

On Streamlit Community Cloud, add the same values under **App settings → Secrets**. Set a monthly spend limit in the Anthropic Console as a backstop.

## Test and evaluate

```bash
pytest                                        # unit tests + headless page tests (no API calls)
python evals/run_evals.py --trials 3          # production routing, 3 trials -> evals/report.md, history.jsonl
python evals/run_evals.py --compare --trials 3  # models x effort levels x routed -> evals/model_comparison.md (~$2)
python evals/run_evals.py --configs sonnet-low,routed
python evals/run_evals.py --demo              # pipeline + rules check on recorded extractions (no API cost)
python scripts/probe_api.py                   # does the prompt cache? what does effort do? (~$0.15)
python scripts/record_demo_cache.py           # record real Claude extractions for demo mode
python scripts/make_samples.py                # regenerate the synthetic quotes and ground truth
```

Each live run appends one line per configuration to `evals/history.jsonl` (git SHA, model, effort, prompt, accuracy, cost, p50/p95, cache hit rate, gate stats), which the AI Scorecard charts over time. `tests/test_pages.py` runs the Streamlit pages headlessly with `AppTest` (every page in the empty, loaded and awarded states, plus the review and comparison guardrails). CI (`.github/workflows/ci.yml`) runs the unit tests and the `--demo` eval on every push and fails if exception recall or verified citations drop below 100%.

## Project structure

```
Home.py                  entrypoint: builds the sidebar navigation (sections, titles, icons) and runs a view
views/                   the pages: Home, New Bid Event, Review & Approve, Comparison, AI Scorecard, Governance
                         (not `pages/`: that folder name switches on Streamlit's legacy auto-navigation)
.streamlit/config.toml   light and dark theme; follows the viewer's OS setting, switchable in Settings
assets/                  logo (light and dark)
reload_guard.py          reloads redeployed bidlens modules on Streamlit
bidlens/
  schemas.py, ingest.py  quote schema; PDF/Excel/text ingestion
  extract.py             Claude extraction with routing and escalation gate
  normalize.py           deterministic date/country/currency/Incoterm normalization
  rules.py               16 exception rules, severities, recommended actions
  costing.py, scoring.py landed cost and weighted scoring
  review.py              buyer edits with validation
  memo.py                template and Claude-drafted award memos
  generate.py            AI-generated test quotes with a hidden answer key
  grading.py             field-by-field comparison with an answer key (evals + generator)
  workflow.py, db.py     orchestration; DuckDB persistence and audit log
  ui.py                  step tracker, next-step logic, risk badges, flag rendering
  credentials.py         secrets for scripts (environment or .streamlit/secrets.toml)
  prompts/               versioned prompts
data/samples/            synthetic supplier quotes + demo RFQ
data/ground_truth/       labelled answers for evals
data/demo_cache/         recorded Claude extractions replayed in demo mode
data/reference/          illustrative tariff, freight and FX tables
evals/                   evaluation harness, results, model_comparison.md, history.jsonl, EVAL_LOG.md
docs/                    REQUIREMENTS.md, GOVERNANCE.md, USER_GUIDE.md
tests/                   pytest suite
scripts/                 sample generation, demo-cache recording, API probe
.github/workflows/       CI: tests + recorded-extraction regression gate
.devcontainer/           GitHub Codespaces setup
```

## Documentation

- [User guide](docs/USER_GUIDE.md): step-by-step instructions, reading flags, generating fresh quotes
- [Requirements](docs/REQUIREMENTS.md): problem statement, process map, user stories, acceptance criteria
- [Governance](docs/GOVERNANCE.md): solution inventory entry, model routing, controls, acceptable use, limitations, escalation
- [Eval log](evals/EVAL_LOG.md): what the evaluations found and what changed

## Limitations

- Tariff, freight and FX tables are illustrative, not live rates.
- Scanned image-only PDFs need OCR.
- Scoring weights are a starting point to calibrate with category managers.
- The eval set is 4 labelled quotes run 3 times per configuration: enough to catch systematic defects and to see run-to-run variation, not to certify accuracy. With 12 runs, p95 latency is close to the maximum.
- The public demo is a shared, ephemeral workspace: everyone sees the same events, and the database resets on redeploy.
- All companies and data in this repository are fictional.

---

Built with [Claude Code](https://claude.com/claude-code).
