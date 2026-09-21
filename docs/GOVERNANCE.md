## Solution inventory entry

| Attribute | Value |
|---|---|
| **Name** | BidLens: Supplier Bid Analyzer |
| **Version** | 0.1.0 (see git tags / commit history) |
| **Business owner** | Procurement Excellence *(demo: portfolio project)* |
| **Technical owner** | Procurement AI & Automation Analyst |
| **Purpose** | Extract commercial terms from supplier quotes, flag exceptions, and normalize bids to landed cost to support (not make) award decisions |
| **Process scope** | RFQ response analysis → bid comparison → draft award memo. Out of scope: supplier selection without buyer approval, contract execution, PO creation |
| **Users** | Buyers and category managers |
| **Data sources** | Supplier quote documents (PDF/XLSX) uploaded by the buyer; reference tables for tariff rates, freight lanes, FX (`data/reference/`) |
| **AI components** | Anthropic Claude, routed by task (see *Model routing* below): extraction (prompt `extract_v2`, followed by deterministic normalization) and optional memo drafting (prompt `memo_v1`) |
| **Deterministic components** | Business rules (`bidlens/rules.py`), landed-cost model (`costing.py`), scoring (`scoring.py`) |
| **Data store** | DuckDB (demo); production target is Snowflake with role-based access |
| **Access** | Demo: public and **not read-only**. Any visitor can create, edit, approve, award and delete events; all visitors share one database, which is discarded on every redeploy. Live mode (own documents sent to Claude) is gated by API key + passcode; AI-generated test scenarios are open to visitors under a shared daily cap |
| **Risk level** | **Medium**: influences sourcing decisions and handles confidential commercial data; mitigated by mandatory human approval |
| **Dependencies** | Anthropic API, Streamlit, pdfplumber, openpyxl, DuckDB |

## Model routing

Each task runs on the cheapest model and reasoning effort expected to do it reliably. Routing and effort are configurable (`BIDLENS_EXTRACT_MODEL`, `BIDLENS_ESCALATION_MODEL`, `BIDLENS_MEMO_MODEL`, `BIDLENS_EXTRACT_EFFORT`, `BIDLENS_ESCALATION_EFFORT`) and any change must be confirmed with `python evals/run_evals.py --compare --trials 3` and recorded in `evals/EVAL_LOG.md`. Every model call logs model, prompt version, effort, uncached / cache-write / cache-read / output tokens, cost and latency.

| Task | Model | Why |
|---|---|---|
| Exception checks, landed cost, scoring | **No LLM** (deterministic code) | Cheapest and fully auditable; these don't need language understanding |
| Quote extraction (first pass) | `claude-sonnet-5`, effort `low`, prompt cached | 4 docs × 3 trials (2026-09-15): same 100% accuracy as Opus and as default effort, at $0.0116/doc and 7.8 s p50 vs $0.0306 and 10.0 s for Opus. The system prompt + output schema (~2,800 tokens) are prompt-cached; 84% of input tokens are cache reads at 0.1× price |
| Quote extraction (escalation) | `claude-opus-5`, default effort | Used only when the first pass fails automatic checks: a citation not found in the document, low confidence on a required field, no price, an unreadable date/country, an unrecognized currency or Incoterm, or a schema/refusal failure. Fired 1 time in 12 low-effort runs (a non-verbatim citation on the bilingual quote) and corrected it. Refusal fallback enabled |
| Award memo draft | `claude-sonnet-5`, effort `medium` | Writes from already-verified data; the template memo remains available with no model |
| Demo test-quote writing | `claude-sonnet-5`, effort `low` | Formatting work only: code chooses every commercial term, and the writer must reproduce them exactly (checked, with one retry) |
| *Not used:* `claude-haiku-4-5` | | The remaining LLM work involves bilingual quotes, European number formats, and tier selection, where errors are expensive. Revisit only if `--compare` shows it matches accuracy |

API or network errors do **not** trigger escalation; only quality failures do. If escalation itself fails, the first-pass result is kept and its weak fields are flagged for the buyer.

## Controls

| Risk | Control |
|---|---|
| Model extracts a wrong value | Every value carries a verbatim citation; citations are checked against the document (`UNVERIFIED_SOURCE`); buyer reviews every quote; edits are logged and tracked as the *field edit rate* KPI |
| Model invents a value | Prompt requires `null` when a value is not stated; missing required fields raise `MISSING_FIELD` instead of being guessed |
| Prompt injection in supplier documents | Documents are wrapped as untrusted data in the prompt; deterministic `SUSPICIOUS_INSTRUCTION` rule flags documents addressed to AI reviewers; demo scenario includes a live example |
| Decision made on unverified data | Comparison and memo are **locked** until every quote is approved or rejected; high-severity flags require explicit acknowledgement |
| AI output treated as final | Memo is labelled *DRAFT - requires buyer verification* and is built only from approved fields |
| Override without rationale | Awarding to a supplier other than the top-scored bid requires a written justification |
| Silent changes to behaviour | Prompts are versioned files; prompt version, model and effort are recorded on every run; evals re-run before changing prompts, models or effort, and every eval run is appended to `evals/history.jsonl` with the git SHA; CI re-runs the recorded-extraction eval on every push |
| Cost overrun | Per-run token and cost logging; API spend on the scorecard; demo mode makes no API calls except AI test-quote generation, which is capped per UTC day for visitors without the live passcode (`BIDLENS_PUBLIC_DAILY_GENERATIONS`, default 10); spend limit set in the Anthropic Console |
| Demo results that look staged | "Generate a fresh scenario" builds unseen quotes: code randomizes terms as a hidden answer key, Claude writes the documents, extraction reads them blind, and the app shows the field-by-field score including misses |
| Auditability | Append-only audit log of event creation, extraction, edits, approvals, rejections, and awards with actor and timestamp |

## Acceptable use

- **Use for:** first-pass extraction and normalization of supplier quotes; identifying exceptions to follow up; drafting an award memo for the buyer to edit.
- **Do not use for:** awarding business without buyer review; legal interpretation of contract terms; evaluating supplier quality, financial health or compliance (the tool has no such data); documents containing personal data.
- **Human verification is mandatory** for: any field flagged high severity, any value edited from the extraction, all figures in the award memo, and tariff/FX assumptions before a real award.

## Known limitations

- Tariff, freight and FX tables are **illustrative**, not live HTS/customs or treasury rates.
- Landed cost is an estimate: duty is applied to goods value only; CFR/CIF are treated as freight-included to destination.
- Scanned (image-only) PDFs are not supported without OCR.
- Payment-term valuation uses a single cost-of-capital assumption.
- Scoring weights are a starting point and should be calibrated with category managers.
- The public demo is a shared, ephemeral workspace, not a system of record: there are no user accounts, every visitor sees and can change or delete the same events (the audit-log name is self-declared), and the DuckDB file is not persisted across redeploys. If a previous server process still holds the file's lock after a restart, the app starts on a fresh database file rather than failing (never when `BIDLENS_DB` names a database).

## Escalation path

1. Extraction or rule defect → log an issue in the GitHub repo with the quote ID from the audit log.
2. Suspected prompt injection or suspicious supplier document → notify Procurement Excellence lead and Cybersecurity.
3. Data exposure concern → stop using live mode, notify Data Privacy / IT Security.

## Lifecycle

Intake → prioritized backlog → build with tests → eval against ground truth (≥95% field accuracy gate) → UAT with buyers → release (tagged commit) → monitor scorecard monthly → re-evaluate on prompt/model change → retire if adoption or accuracy falls below target.
