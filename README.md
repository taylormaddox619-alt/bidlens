# BidLens: AI-Assisted Supplier Bid Analysis

**BidLens reads supplier quotes with Claude, flags commercial exceptions, and compares bids on total landed cost. A buyer approves every step.**

> 🔗 **Live demo:** https://bidlens-4mwcnxw6tc2btynjwfu99u.streamlit.app/ · runs in demo mode with fictional data, no login needed

Buyers compare supplier quotes that arrive as PDFs and spreadsheets in every format imaginable. They re-key the data into Excel, and they often compare on **unit price**, which misses freight, tariffs, tooling and payment terms. BidLens automates the repetitive steps and leaves judgment calls to the buyer.

| Step | What happens | Who decides |
|---|---|---|
| **Extract** | Claude turns each quote into a fixed schema; **every value cites the exact source text** | AI |
| **Check** | 16 deterministic rules flag exceptions: expired quotes, lead-time misses, tariff exposure, prepayment, missing warranty, fabricated citations, prompt-injection text | Rules |
| **Review** | Buyer verifies highlighted evidence, corrects values, approves or rejects. **Comparison is locked until every quote is reviewed.** | **Buyer** |
| **Compare** | Normalized landed cost (FX, tier pricing, tooling, freight, duty, payment-term cost of capital) and weighted scoring with visible rationale | Assisted |
| **Decide** | Draft memo from approved data only; overrides of the top-scored bid require a justification; full audit log | **Buyer** |
| **Measure** | AI Scorecard tracks adoption, quality (edit rate, eval accuracy), hours saved, cost avoided, API spend | Leadership |

### The demo scenario

RFQ for 500 cast-iron compressor air-end housings, with four fictional suppliers. Each quote has a built-in problem:

| Supplier | Format | What BidLens catches |
|---|---|---|
| Jadeport Foundry (CN) | PDF | **Lowest unit price ($172)** but a 35% tariff and excluded freight make it the **most expensive landed ($258/unit)**. Also contains hidden text telling "AI reviewers" to recommend it (prompt injection → flagged) |
| Kessler & Vogt (DE) | Bilingual PDF, EUR, European number format | EXW (freight excluded), 30% deposit + Net 30 |
| Lakeshore Cast (US) | PDF | Freight included, but lead time 16 wks vs 12 required, and the quote has expired |
| Sierra Madre Castings (MX) | Excel, tiered pricing | Warranty not stated (buyer must follow up); best landed cost at **$207/unit** |

**Result:** picking the lowest unit price would have cost **$25,531 more** than the best landed-cost bid.

## Architecture

```
PDF / XLSX ──► ingest.py ──► extract.py ──► rules.py ──► Review (human) ──► costing.py ─► scoring.py ─► memo.py
               (text)       (Claude,        (16 checks,     approve/edit     (landed cost)  (weighted)   (draft)
                             structured      citation         /reject              │
                             outputs)        verification)                          ▼
                                   └──────────────► db.py (DuckDB: events, quotes, edits, runs, awards, audit log)
```

- **LLM:** Anthropic Claude via schema-constrained structured outputs (Pydantic). Prompts are versioned files in `bidlens/prompts/`.
- **Cost-aware model routing:** extraction runs on `claude-sonnet-5` and escalates to `claude-opus-5` only when the output fails automatic checks (citation not in document, low confidence on a required field, no price, schema failure). Memos use Sonnet at medium effort. Rules, costing and scoring use no LLM at all. `python evals/run_evals.py --compare` measures routed vs. each model alone on accuracy, cost and latency.
- **Guardrails:** verbatim citations checked against the source, null-not-guess instructions, untrusted-document framing plus a deterministic injection detector, mandatory human approval, a locked comparison and a DRAFT-labelled memo.
- **Data:** DuckDB with all SQL isolated in `bidlens/db.py`, so it can move to Snowflake.
- **UI:** Streamlit multipage app.
- **Quality:** pytest suite (rules, costing, scoring, review edits, normalization, routing, ingest, demo cache) and an evaluation harness that scores extraction against labelled ground truth. [EVAL_LOG.md](evals/EVAL_LOG.md) shows how eval runs found two real defects (a prompt contradiction and an unparsed expiry date that hid an expired quote) and how they were fixed.

**Latest eval (4 labelled quotes):** routed extraction matched Opus-only at 100% field accuracy and 100% exception recall, for **$0.021 vs $0.043 per quote**. See [model_comparison.md](evals/model_comparison.md).

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run Home.py
```

Demo mode works without an API key. To enable **Live mode**, create `.streamlit/secrets.toml`:

```toml
ANTHROPIC_API_KEY = "sk-ant-..."
BIDLENS_LIVE_PASSCODE = "choose-a-passcode"   # optional: gate live mode on a public deployment
```

## Test and evaluate

```bash
pytest                                  # unit tests
python evals/run_evals.py               # live extraction accuracy vs ground truth -> evals/report.md
python evals/run_evals.py --compare     # routed vs. Sonnet-only vs. Opus-only -> evals/model_comparison.md
python evals/run_evals.py --demo        # pipeline + rules check on recorded extractions (no API cost)
python scripts/record_demo_cache.py     # record real Claude extractions for demo mode
python scripts/make_samples.py          # regenerate the synthetic quotes and ground truth
```

## Project structure

```
Home.py, pages/          Streamlit UI (New Bid Event, Review & Approve, Comparison, AI Scorecard, Governance)
bidlens/                 schemas, ingest, extract, rules, costing, scoring, review, memo, db, workflow
bidlens/prompts/         versioned prompts
data/samples/            synthetic supplier quotes + demo RFQ
data/ground_truth/       labelled answers for evals
data/reference/          illustrative tariff, freight and FX tables
evals/                   evaluation harness and report
docs/                    REQUIREMENTS.md, GOVERNANCE.md, USER_GUIDE.md
tests/                   pytest suite
```

## Documentation

- [Requirements](docs/REQUIREMENTS.md): problem statement, process map, user stories, acceptance criteria
- [Governance](docs/GOVERNANCE.md): solution inventory entry, controls, acceptable use, limitations, escalation
- [User guide](docs/USER_GUIDE.md): how buyers use the tool

## Limitations

Tariff, freight and FX tables are illustrative, not live rates. Scanned image-only PDFs need OCR. Scoring weights are a starting point to calibrate with category managers. All companies and data in this repository are fictional.

---

Built with [Claude Code](https://claude.com/claude-code).
