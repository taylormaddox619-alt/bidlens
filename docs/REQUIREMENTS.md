# Requirements

## Problem statement

Buyers receive supplier quotes as PDFs and spreadsheets in inconsistent formats. Building a bid comparison means re-keying prices and terms by hand (about 20–30 minutes per quote), and the comparison is often done on **unit price** rather than **total landed cost**, so freight, tariffs, tooling and payment terms get missed. Exceptions such as expired quotes, missed lead times and prepayment demands are easy to overlook.

## Process map (current → future)

| Step | Current state | Future state | Automated? |
|---|---|---|---|
| Receive quotes | Email attachments | Upload to bid event | Manual |
| Read and key in terms | Buyer re-types into Excel | Claude extracts to schema with citations | **Automated** |
| Check exceptions | Buyer's memory and experience | Rules engine flags 16 exception types | **Automated** |
| Verify data | Rarely done systematically | Buyer reviews flagged fields and approves | **Human judgment** |
| Normalize cost | Often skipped (unit price only) | Landed cost model (FX, freight, duty, tooling, terms) | **Automated** |
| Weigh trade-offs | Implicit | Weighted scoring with visible rationale; weights adjustable | Assisted |
| Decide award | Buyer / category manager | Buyer; override requires justification | **Human judgment** |
| Document decision | Email / memo from scratch | Draft memo from approved data; audit log | Assisted |

## User stories and acceptance criteria

**US-1 Extract quote terms.** *As a buyer, I want supplier quotes converted into a standard set of fields so I don't re-key data.*
- Given a PDF or XLSX quote, the system returns all 17 commercial fields plus price tiers and exceptions.
- Every non-null value includes verbatim source text; values not in the document are null, not guessed.
- If extraction fails, the buyer can enter the quote manually or reject it.

**US-2 Flag exceptions.** *As a buyer, I want non-standard terms highlighted so I don't miss them.*
- Rules flag: missing required field, citation not found, low confidence, currency mismatch, unknown currency, lead time over requirement, expired quote, payment terms under standard, prepayment, freight excluded, tariff exposure, unknown origin, MOQ over quantity, price outlier, supplier exceptions, and instructions addressed to AI reviewers.
- Each flag has a severity (high/medium/low) and a plain-English message.
- The demo scenario raises exactly the expected flags on each sample (automated test).

**US-3 Review and approve.** *As a buyer, I want to verify and correct extracted data before it is used.*
- The source document is shown with cited text highlighted.
- Edits are validated by type, logged with old/new value and actor, and the rules re-run.
- Approval is blocked while edits are unsaved or unit price/currency are missing; high-severity flags require explicit acknowledgement.

**US-4 Compare on landed cost.** *As a category manager, I want bids compared on total landed cost and weighted criteria.*
- The comparison is unavailable until every quote is approved or rejected, and at least 2 are approved.
- Landed cost includes FX conversion, tier pricing at RFQ quantity, tooling, freight (quoted, included, or estimated), duty, and payment-term cost of capital.
- Weights are adjustable and the score calculation is shown.

**US-5 Document the decision.** *As a buyer, I want a draft memo and a recorded decision for audit.*
- The memo is marked DRAFT and built only from approved data.
- Choosing a supplier other than the top-scored bid requires a justification.
- The award, the naive (lowest unit price) alternative, and the cost avoided are stored.

**US-6 Measure value.** *As Procurement Excellence leadership, I want to see adoption, quality and value.*
- Scorecard shows events, quotes, extraction success, field edit rate, exception rate, estimated hours saved, cost avoided, API spend, and offline eval results.

## Non-functional requirements

- Demo mode runs with no API key and no external calls.
- API keys are read from secrets or environment only, never committed.
- Model, prompt version, tokens, cost and latency are logged per run.
- Unit tests cover rules, costing, scoring, ingest and the demo cache; eval harness measures extraction against ground truth.
