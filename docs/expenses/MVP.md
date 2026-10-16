# Mizu Kakeibo MVP

## Document authority

This document is the source of truth for the MVP's product scope: the problem being solved, the user-visible workflow, the required capabilities, what is out of scope, and the definition of done.

It does not track implementation status or prescribe detailed schemas and invariants. Use the [delivery roadmap](ROADMAP.md) for sequencing, dependencies, and completion gates, and the [inbox-to-expense contract](inbox-expense-contract.md) for technical behavior.

## Product idea

Mizu Kakeibo watches the Gmail inbox for emails that may represent expenses. Every new inbox email is considered, so the user does not need to know which banks, merchants, or payment applications will send notifications in advance. The only exceptions are a fixed skip filter for mail that has not carried expenses: Gmail's Promotions and Social tabs and job-alert senders.

The system uses one full-message analyser to decide whether an email represents an expense. Confirmed expenses are written to a personal ledger, uncertain messages are held for review, and other messages are marked as processed without creating a transaction. Gmail remains the source of truth.

## Core workflow

```text
New Gmail inbox message
  -> Skip without analysis if it is in Promotions or Social,
     or comes from a job-alert sender
  -> Skip if the Gmail message ID was already processed,
     unless re-evaluation was explicitly requested
  -> Retrieve the full message
  -> Determine whether an expense occurred
      -> Expense: extract and log the transaction
      -> Not an expense: record the decision without a transaction
      -> Uncertain or incomplete: add the message to the review queue
```

Every new message that passes the skip filter follows the same path. There is no Gmail-label fast path or separate relevance model.

The skip filter was adopted on 2026-10-16 because analysing every message used most of a 16 GB laptop's memory for about a minute per message, and most messages were promotions and job alerts. In the 33-message evaluation set and the first 78 analysed inbox messages, no expense came from Promotions, Social, or a job-alert sender, and the only review item among them was a promotion too long to analyse. The trade-off is accepted: a receipt that Gmail files under Promotions or Social is never analysed. Promotions and Social are left out of the inbox search itself, which also keeps each sync's search smaller; they remain in their Gmail tabs. Skipped job alerts are reported in each sync's result.

## Message decisions

Every successfully processed Gmail message ends with one decision:

- `expense` means an expense was identified and a transaction was logged.
- `not_expense` means no transaction was logged.
- `needs_review` means the analyser could not decide safely or could not extract the required transaction values.

Version 0 focuses on clear completed expenses. Refunds, reversals, transfers, pending charges, bills, statements, and other policy-dependent cases remain review items; defining their permanent accounting treatment is deferred.

A message that reports no payment by the user is not an expense and does not need review: a failed or declined payment, a cancellation, an exchange-rate or price alert, a shipping or delivery update for an order, or a social or account notification. A future or recurring fee, such as the price after a trial ends, is never logged as a completed expense. A confirmation whose paid amount appears only in an attachment goes to review, and so does an order-placed notice that shows an order total but does not confirm payment, because the order's receipt may arrive separately and must not be logged twice.

The merchant is the business the user paid, named by its brand. When a delivery, ride-hailing, or other app takes the payment, the merchant is the app, not the restaurant, store, or driver.

Every expense is recorded in the currency the message shows and is never converted by the analyser. For this personal inbox, an amount with no currency marker at all is assumed to be Malaysian ringgit. A symbol shared by several currencies, such as a bare `$`, does not establish the currency on its own: unless the message states the currency elsewhere, the expense goes to review. The [inbox-to-expense contract](inbox-expense-contract.md) defines these rules.

Converting a foreign-currency expense to ringgit needs a real exchange rate, so it is application work rather than analyser work. It is deferred until spending totals or reporting are added. Until then, the ledger keeps each expense's original amount and currency.

When an otherwise clear completed expense has no transaction date in the message, Version 0 uses the email's normalized received date. An explicit transaction date takes precedence. The [inbox-to-expense contract](inbox-expense-contract.md) defines the fallback and its limits.

## Minimum record

Every successful analysis retains the source provider and Gmail message ID, which together are the canonical source reference. It also retains the decision and sanitized reason, any missing fields, and the analyser versions needed for reproducibility. A transaction is stored only for an accepted expense. Its amount uses the currency's integer minor unit so the ledger does not depend on floating-point arithmetic.

The authoritative field definitions and invariants live in the [inbox-to-expense contract](inbox-expense-contract.md).

## MVP components

### Gmail retrieval

- Search new inbox messages.
- Start from a fixed date: inbox messages received before 2026-10-16 in Malaysia time are never processed. The user moved the start from 2026-10-10 to 2026-10-16, the day the skip filter and the lighter analyser model were released, so the remaining backlog of earlier messages is not processed; earlier decisions and ledger rows are kept.
- Process new messages in bounded batches, oldest first; the next sync continues with any that remain.
- Keep the Gmail message ID as the unique source identifier.
- Skip processed IDs unless the user explicitly requests re-evaluation.
- Leave Gmail's Promotions and Social tabs out of the search, and skip messages from job-alert senders without retrieving or analysing them, reporting them in the sync result.
- Retrieve the full content of every other new message before analysis.
- Normalize and analyse supported message-body text only; do not fetch or analyse attachments in Version 0.
- Preserve the Gmail message ID so the original message can be opened during review.
- Follow that contract for retrieval, normalization, analysis, validation, and persistence.

### Expense analyser

- Receive one normalized full message at a time.
- Return only `expense`, `not_expense`, or `needs_review`.
- Extract merchant, integer minor-unit amount, currency, and transaction date only for a confirmed expense.
- Return `needs_review` when the decision is uncertain, required values are missing, or necessary content cannot be interpreted safely.
- Send a message that is too long for the analyser to read in full to review, rather than leaving it to fail on every sync.
- Produce structured output that can be validated before anything is persisted.
- Follow the versioned model, prompt, and runtime configuration in the inbox-to-expense contract.
- Run light enough that the laptop stays usable during a sync, and release the model's memory soon after the sync ends.

On 2026-10-16 the user set speed and laptop load ahead of small accuracy gains: the analyser must not miss or falsely log expenses in evaluation, but an occasional unnecessary review or merchant-name error is acceptable for now and can be corrected in review. Improving accuracy further is deferred.

### Personal ledger

- Start with a private CSV file for manual validation.
- Store one row per confirmed expense.
- Store processed message decisions separately or alongside the ledger so non-expenses are not analyzed repeatedly.
- Allow corrections without changing the source email.

### Review

- Show the source email beside the proposed decision and transaction.
- Allow the user to accept an expense, mark it as not an expense, or correct extracted values.
- Preserve the Gmail message ID and the original decision after correction.

## External payment applications

No payment-application integration is required when the application sends receipts or transaction notifications into Gmail. Those messages enter the inbox-wide workflow even when the sender is unknown and no Gmail filter exists.

Applications that send no email are outside the MVP and may require direct integrations later.

## Deferred improvements

These improvements wait until the MVP is complete:

- **Oversized messages.** Analyse a message that is too long for the analyser instead of sending it to review.
- **Sync performance.** Make each sync faster and lighter. The first real sync, on 2026-10-15, took about a minute per message, and most of the messages it analysed were job alerts and promotions. The skip filter and a smaller analyser model were pulled forward from this work on 2026-10-16.
- **Analyser accuracy.** Reduce unnecessary reviews and merchant-name errors once the end-to-end daily workflow is in use.

## Out of scope

The MVP does not require:

- A model-based metadata relevance gate. The skip filter is a fixed rule, not a model.
- Gmail labels or filters for financial senders.
- A complete definition of every financial event type before validation.
- Attachment retrieval or analysis.
- Direct bank or payment-application connections.
- A dedicated forwarding address or inbound email provider.
- Categories, budgets, reporting, or visual dashboards.
- Converting foreign-currency expenses to ringgit.
- Gmail push notifications instead of scheduled polling.
- Hosted deployment or PostgreSQL storage.
- Multi-user accounts, public onboarding, or native mobile applications.
- Subscription billing or a custom-trained model.

## Definition of done

The MVP is complete when one agent request:

1. Finds new inbox messages and retrieves each new message in full.
2. Does not reprocess Gmail message IDs already recorded.
3. Sends every new message that passes the skip filter through the same expense analyser.
4. Records each successful analysis as `expense`, `not_expense`, or `needs_review`.
5. Logs a traceable ledger row only for accepted expenses.
6. Presents uncertain decisions and missing transaction values for review.

The first validation run established the full-message baseline, which showed a meaningful latency problem; the skip filter is the response. Further metadata routing and Gmail labels may be evaluated later on the same terms.
