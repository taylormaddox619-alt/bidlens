# BidLens user guide

*For buyers and category managers. About 5 minutes to read.*

## Before you start

- Use BidLens for **first-pass analysis** of supplier quotes. You remain accountable for the award.
- Upload only documents you are allowed to process with approved AI tools. The public demo accepts **fictional data only**.
- Everything you do is recorded in the audit log under the name in the sidebar.

## 1. Create a bid event

**New Bid Event** → *Create a custom bid event*. Enter the quantity, required lead time and standard payment terms. The rules check each quote against these values, so get them right.

Try it first with **Load demo scenario**.

## 2. Upload quotes

Upload PDF, XLSX, TXT or CSV quotes and click **Extract**. Each quote gets a status:

- 🟡 **Needs review**: extracted, waiting for you
- ❌ **Extraction failed**: the file couldn't be read. Enter the terms manually or reject the quote.

## 3. Review and approve each quote

On **Review & Approve**:

1. **Read the exceptions** at the top. 🔴 high = must be resolved or consciously accepted; 🟠 medium = check; 🔵 low = FYI.
2. **Check the evidence.** The source document on the right highlights the text each value came from. In the grid:
   - ✓ **cited**: value is backed by text in the document
   - ⚠ **citation not found**: the AI cited text that isn't in the document; verify it carefully
   - **not in document**: the supplier didn't state it; follow up if required
   - ✏️ **buyer entered**: you changed it
3. **Correct** any wrong value in the *Value* column. Numbers accept `$6,500` or `6500`; dates must be `YYYY-MM-DD`. Click **Save edits**; the rules re-run automatically.
4. **Approve** or **Reject**. If high-severity exceptions remain, you must tick the acknowledgement box first.

**When the AI output must not be used without verification:** any 🔴 flag, any ⚠ citation, anything addressed to "AI reviewers" in a document, and any value that drives the award (price, currency, lead time, Incoterm, origin).

## 4. Compare

**Comparison** unlocks once every quote is approved or rejected.

- **Landed cost** = goods (at the tier price for your quantity) + tooling + freight + estimated duty ± payment-terms adjustment.
- *Freight est.* means the supplier excluded freight and BidLens used a lane estimate. Get a real freight quote before awarding.
- Adjust the **scoring weights** in the sidebar to match the category strategy. Open *How each score was calculated* to see the reasoning.

## 5. Decide

- **Draft memo** creates a starting point built only from approved data. Edit it before sharing.
- **Record award decision.** If you choose a supplier other than the top-scored bid, write a justification. This is normal and expected when you have information the tool doesn't, such as quality history or strategic relationships.

## Getting help

- Wrong extraction or rule → report it with the quote's filename and time (see the Audit log on AI Scorecard).
- Suspicious document → stop and notify the Procurement Excellence lead.
