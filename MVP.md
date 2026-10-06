# Mizu Kakeibo MVP

## Product goal

Mizu Kakeibo turns financial emails already collected in Gmail into a traceable personal transaction ledger. The MVP validates that an agent can reliably extract transactions from real email formats before a standalone application or automated ingestion service is built.

The smallest useful product is a Gmail label, Gmail access through MCP, structured AI extraction, and a CSV ledger. Gmail remains the inbox and source of truth.

## User outcome

With one command or agent request, the user can:

- Find newly labeled financial emails.
- Convert clear emails into structured transactions.
- Avoid reprocessing emails already recorded in the ledger.
- Identify uncertain or possibly duplicated transactions for review.
- Trace every ledger entry back to its source Gmail message.

## End-to-end workflow

```text
Financial email arrives in Gmail
  -> Gmail filter applies the Finance Tracker label
  -> Agent searches the label
  -> Gmail MCP returns the full email
  -> AI extracts and classifies a transaction
  -> Transaction is added to the ledger
  -> Ambiguous results are marked for review
```

Gmail owns the original messages. The ledger stores extracted records and may be corrected without changing the source email.

## MVP scope

### Gmail labeling

- Create a `Finance Tracker` label.
- Configure Gmail filters for known banks, card providers, wallets, and merchants.
- Allow the user to label exceptions manually.

### Email retrieval

- Search messages carrying the `Finance Tracker` label.
- Read the full message body and supported attachments.
- Use the Gmail message ID as the unique source identifier.
- Skip any message ID already present in the ledger.

### Transaction extraction

For each eligible email, extract one structured transaction with this contract:

```json
{
  "source_email_id": "gmail-message-id",
  "type": "expense",
  "merchant": "Grab",
  "amount_minor": 4250,
  "currency": "MYR",
  "transaction_date": "2026-10-06",
  "category": "transport",
  "payment_source": "Maybank Visa",
  "confidence": 0.97,
  "needs_review": false
}
```

`amount_minor` stores the value in the currency's minor unit so the ledger does not depend on floating-point arithmetic. `source_email_id` links the extracted record to Gmail and provides the primary deduplication key.

### Personal ledger

- Store one row per transaction in CSV for the first validation run.
- Preserve the Gmail message ID and a link or reference to the source email.
- Treat the source email ID as unique so the same message cannot be recorded twice.
- Allow ledger corrections without modifying the email.
- Support CSV export if a later ledger format replaces CSV.

### Review flow

- Automatically accept results when all required fields are present and the extraction is unambiguous.
- Set `needs_review` when an amount is missing, a date is ambiguous, a currency is unknown, or the transaction may be a duplicate.
- Present the source email alongside the proposed transaction during review.
- Keep reviewed corrections in the ledger while preserving the original email reference.

### Basic reporting

The ledger must support:

- Current-month spending.
- Spending grouped by category.
- Refunds.
- Transactions awaiting review.

## Delivery stages

### Version 0 manual validation

1. Label 20 to 50 existing financial emails.
2. Ask the agent to retrieve and parse them.
3. Save the structured results to CSV.
4. Review extraction errors and refine the extraction instructions.

This stage confirms whether the user's actual email formats can be processed accurately enough to continue.

### Version 0.5 repeatable workflow

1. Store processed Gmail message IDs.
2. Fetch only new labeled messages.
3. Add duplicate detection.
4. Maintain a review queue.
5. Run the workflow manually once a day or once a week.

### Version 1 automated personal service

Build an automated service only after the manual workflow is useful. That service may replace the interactive MCP step with Gmail API authentication, scheduled polling or Gmail push notifications, background processing, structured LLM calls, durable database storage, and a small private dashboard.

## Out of scope

The MVP does not include:

- A dedicated forwarding address or inbound email provider.
- Multi-user accounts or public onboarding.
- A native mobile application.
- Direct bank connections.
- Complex budgeting features.
- Subscription billing.
- A custom-trained model.
- A standalone web application or background service.

## Acceptance criteria

The MVP is complete when one command or agent request:

1. Finds new financial emails carrying the `Finance Tracker` label.
2. Produces ledger-ready transactions for clear emails.
3. Does not reprocess Gmail message IDs already in the ledger.
4. Flags uncertain or possibly duplicated transactions for review.
5. Updates the personal CSV ledger.
6. Keeps every transaction traceable to its Gmail message.

The first validation run must use 20 to 50 real financial emails and record the extraction errors found. Those errors determine the extraction-instruction changes required before the repeatable workflow begins.
