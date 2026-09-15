# BidLens user guide

*For buyers and category managers. About 5 minutes to read.*

## Before you start

- Use BidLens for **first-pass analysis** of supplier quotes. You remain accountable for the award.
- Upload only documents you are allowed to process with approved AI tools. The public demo accepts **fictional data only**.
- Everything you do is recorded in the audit log under the name in the sidebar.

## Finding your way: the step tracker

Every workflow page starts with a four-step tracker for the active bid event (chosen in the sidebar):

**1 · Add quotes → 2 · Review & approve → 3 · Compare & award → 4 · Measure**

- ✅ means done, 👉 means the page you're on, ⬜ means still to do.
- Under each step, a short status shows progress, e.g. "2 of 4 reviewed · 🔴 1 high-risk".
- The blue **Next** button names the single next action ("Next: review 3 quotes →") and takes you there. If you're unsure what to do, click it.

## Reading the flags

The rules grade every issue they find:

| Level | Meaning | What you do |
|---|---|---|
| 🔴 **High risk** | Could make the award wrong or unsafe | Resolve it, or explicitly accept it before approving |
| 🟠 **Medium risk** | Affects cost or terms | Check it and follow up with the supplier |
| 🔵 **Info** | For awareness | Usually nothing |

Each flag comes with a **"What to do"** line, for example "Ask the supplier to revalidate the quote before any award."

Wherever quotes are listed, risk badges like `🔴 2 high · 🟠 1 medium` tell you where to look first.

## 1. Create a bid event and add quotes

**New Bid Event → Quick start** offers two ways to start:
- **Load demo scenario:** a prepared RFQ with four sample quotes, each with a built-in problem.
- **🎲 Generate a fresh scenario:** a random RFQ with AI-written quotes from suppliers nobody has seen (see below).

For real work, use **Create a custom bid event**. Enter the quantity, required lead time and standard payment terms; the rules check every quote against these values. Then upload quotes (PDF, Excel, text) at **Add quotes** and click **Extract**.

The **Quotes in this event** table lists pending, highest-risk quotes first. A red banner names any quote with high-risk issues.

## 2. Review and approve each quote

**Review & Approve** opens the pending quote with the highest risk first. For each quote:

1. **Read the colored boxes.** The red box lists high-risk issues and the amber box medium-risk ones, each with what to do.
2. **Check the evidence.** The document on the right highlights the text each value came from. In the grid:
   - 🔴 or 🟠 next to a field means that field has a flag;
   - ✓ **cited**: the value is backed by text in the document;
   - ⚠ **citation not found**: verify it carefully;
   - **not in document**: the supplier didn't state it;
   - ✏️ **buyer entered**: you changed it.
3. **Correct** any wrong value (double-click it). Numbers accept `$6,500` or `6500`; dates must be `YYYY-MM-DD`. Click **Save edits**; the rules re-run.
4. **Approve** or **Reject.** If high-risk issues remain, the app lists them and you must tick the box to accept them.

After each decision, a banner tells you who's next ("Next up: Lakeshore Cast Components (🔴 2 high) · 2 left"), and that quote opens automatically. When every quote is done, click **Next: compare bids**.

**Never use AI output without verification** for: any 🔴 flag, any ⚠ citation, anything addressed to "AI reviewers" in a document, and any value that drives the award (price, currency, lead time, Incoterm, origin).

## 3. Compare

**Comparison** unlocks once every quote is approved or rejected. While it's locked, the page lists the quotes still waiting, with their risk badges.

- **Landed cost** = goods (at the tier price for your quantity) + tooling + freight + estimated duty ± payment-terms adjustment.
- *Freight est.* means the supplier excluded freight and BidLens used a lane estimate. Get a real freight quote before awarding.
- **Red flags by supplier** summarizes the high and medium issues each approved quote still carries.
- Adjust the **scoring weights** in the sidebar to match the category strategy. Open *How each score was calculated* to see the reasoning.

## 4. Decide

- The **Award recommendation** shows the top-scored supplier's open risks. A red banner appears if it has high-risk issues.
- **Draft memo** creates a starting point built only from approved data. Edit it before sharing.
- **Record award decision.** If you choose a supplier other than the top-scored bid, write a justification. This is normal when you know things the tool doesn't, such as quality history.

## Generating fresh test quotes (demo)

**🎲 Generate a fresh scenario** (or **Generate supplier quotes for this RFQ with AI** on an empty event) shows the tool isn't tuned to the sample quotes:

1. Code randomly chooses each supplier's terms and keeps them as a hidden answer key.
2. Claude writes realistic quote documents from those terms.
3. The normal pipeline reads them without seeing the answer key.

Afterwards, New Bid Event shows an **answer-key check** (fields extracted correctly, and any misses). Each generated quote on Review & Approve has a full field-by-field comparison. Generation takes about a minute; public visitors share a daily limit.

## Getting help

- Wrong extraction or rule → report it with the quote's filename and time (see the audit log on AI Scorecard).
- Suspicious document → stop and notify the Procurement Excellence lead.
