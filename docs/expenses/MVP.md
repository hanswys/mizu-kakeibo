# Mizu Kakeibo MVP

## Document authority

This document is the source of truth for the MVP's product scope: the problem being solved, the user-visible workflow, the required capabilities, what is out of scope, and the definition of done.

It does not track implementation status or prescribe detailed schemas and invariants. Use the [delivery roadmap](ROADMAP.md) for sequencing, dependencies, and completion gates, and the [inbox-to-expense contract](docs/inbox-expense-contract.md) for technical behavior.

## Product idea

Mizu Kakeibo watches the Gmail inbox for emails that may represent expenses. Every new inbox email is considered, so the user does not need to know which banks, merchants, or payment applications will send notifications in advance.

The system uses one full-message analyser to decide whether an email represents an expense. Confirmed expenses are written to a personal ledger, uncertain messages are held for review, and other messages are marked as processed without creating a transaction. Gmail remains the source of truth.

## Core workflow

```text
New Gmail inbox message
  -> Skip if the Gmail message ID was already processed,
     unless re-evaluation was explicitly requested
  -> Retrieve the full message
  -> Determine whether an expense occurred
      -> Expense: extract and log the transaction
      -> Not an expense: record the decision without a transaction
      -> Uncertain or incomplete: add the message to the review queue
```

Every new message follows the same path. The initial design has no metadata gate, Gmail-label fast path, or separate relevance model.

## Message decisions

Every successfully processed Gmail message ends with one decision:

- `expense` means an expense was identified and a transaction was logged.
- `not_expense` means no transaction was logged.
- `needs_review` means the analyser could not decide safely or could not extract the required transaction values.

Version 0 focuses on clear completed expenses. Refunds, reversals, transfers, pending charges, bills, statements, and other policy-dependent cases remain review items; defining their permanent accounting treatment is deferred.

For this personal inbox, Version 0 assumes Malaysia as the default currency context when a completed expense has no explicit currency code. An explicit currency in the message takes precedence. The [inbox-to-expense contract](docs/inbox-expense-contract.md) defines how that assumption applies to ambiguous symbols.

When an otherwise clear completed expense has no transaction date in the message, Version 0 uses the email's normalized received date. An explicit transaction date takes precedence. The [inbox-to-expense contract](docs/inbox-expense-contract.md) defines the fallback and its limits.

## Minimum record

Every successful analysis retains the source provider and Gmail message ID, which together are the canonical source reference. It also retains the decision and sanitized reason, any missing fields, and the analyser versions needed for reproducibility. A transaction is stored only for an accepted expense. Its amount uses the currency's integer minor unit so the ledger does not depend on floating-point arithmetic.

The authoritative field definitions and invariants live in the [inbox-to-expense contract](docs/inbox-expense-contract.md).

## MVP components

### Gmail retrieval

- Search new inbox messages.
- Keep the Gmail message ID as the unique source identifier.
- Skip processed IDs unless the user explicitly requests re-evaluation.
- Retrieve the full content of every new message before analysis.
- Normalize and analyse supported message-body text only; do not fetch or analyse attachments in Version 0.
- Preserve the Gmail message ID so the original message can be opened during review.
- Follow that contract for retrieval, normalization, analysis, validation, and persistence.

### Expense analyser

- Receive one normalized full message at a time.
- Return only `expense`, `not_expense`, or `needs_review`.
- Extract merchant, integer minor-unit amount, currency, and transaction date only for a confirmed expense.
- Return `needs_review` when the decision is uncertain, required values are missing, or necessary content cannot be interpreted safely.
- Produce structured output that can be validated before anything is persisted.
- Follow the versioned model, prompt, and runtime configuration in the inbox-to-expense contract.

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

## Out of scope

The MVP does not require:

- A metadata-only relevance gate.
- Gmail labels or filters for financial senders.
- A complete definition of every financial event type before validation.
- Attachment retrieval or analysis.
- Direct bank or payment-application connections.
- A dedicated forwarding address or inbound email provider.
- Categories, budgets, reporting, or visual dashboards.
- Gmail push notifications instead of scheduled polling.
- Hosted deployment or PostgreSQL storage.
- Multi-user accounts, public onboarding, or native mobile applications.
- Subscription billing or a custom-trained model.

## Definition of done

The MVP is complete when one agent request:

1. Finds new inbox messages and retrieves each new message in full.
2. Does not reprocess Gmail message IDs already recorded.
3. Sends every new message through the same expense analyser.
4. Records each successful analysis as `expense`, `not_expense`, or `needs_review`.
5. Logs a traceable ledger row only for accepted expenses.
6. Presents uncertain decisions and missing transaction values for review.

The first validation run should establish the full-message baseline. Metadata routing and Gmail labels may be evaluated later only if the baseline shows a meaningful cost, latency, or privacy problem.
