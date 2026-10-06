# Mizu Kakeibo MVP

## Product idea

Mizu Kakeibo watches the Gmail inbox for emails that may represent expenses. Every new inbox email is considered, so the user does not need to know which banks, merchants, or payment applications will send notifications in advance.

The system decides whether an email represents an expense. Confirmed expenses are written to a personal ledger, uncertain messages are held for review, and other messages are marked as processed without creating a transaction. Gmail remains the source of truth.

## Core workflow

```text
New Gmail inbox message
  -> Skip if the Gmail message ID was already processed,
     unless re-evaluation was explicitly requested
  -> Check for the optional Finance Tracker label
      -> Labeled: go directly to full expense determination
      -> Unlabeled: run lightweight relevance analysis
          -> Clearly irrelevant: record not_expense
          -> Possibly financial or uncertain: inspect the full email
  -> Determine whether an expense occurred
      -> Expense: extract and log the transaction
      -> Not an expense: record the decision without a transaction
      -> Uncertain: add the message to the review queue
```

The lightweight analysis should use the minimum useful content, such as sender, subject, and snippet. Full message bodies and supported attachments are needed only for labeled, possibly financial, or uncertain messages.

## Finance Tracker label

The `Finance Tracker` label is optional. It improves routing but is not required for discovery.

- Known banks, merchants, or payment applications may be labeled automatically with Gmail filters.
- A labeled message skips lightweight relevance analysis but still goes through expense determination.
- The label never proves that an expense occurred. A labeled newsletter, OTP, declined-payment alert, or statement notice may still be classified as not an expense.
- The user may apply the label later to force full re-evaluation of a message.
- An unknown sender can still be discovered because unlabeled inbox messages are analyzed.

## Message decisions

Every processed Gmail message ends with one decision:

- `expense` means an expense was identified and a transaction was logged.
- `not_expense` means no transaction was logged.
- `needs_review` means the system could not decide safely or could not extract the required transaction values.

The exact rules for refunds, reversals, transfers, pending charges, bills, statements, and other edge cases will be decided from real examples during validation. The MVP must preserve these cases for review rather than silently inventing a permanent accounting rule.

## Minimum record

Every processed message records enough information to prevent reprocessing and explain the outcome:

```json
{
  "source_email_id": "gmail-message-id",
  "source_email_ref": "gmail-message-reference",
  "decision": "expense",
  "confidence": 0.97,
  "needs_review": false,
  "reason": "Completed card purchase",
  "transaction": {
    "merchant": "Grab",
    "amount_minor": 4250,
    "currency": "MYR",
    "transaction_date": "2026-10-06"
  }
}
```

`transaction` is present only when an expense is accepted. `amount_minor` stores the value in the currency's minor unit so the ledger does not depend on floating-point arithmetic.

## MVP components

### Gmail retrieval

- Search new inbox messages, not only messages carrying a finance label.
- Keep the Gmail message ID as the unique source identifier.
- Skip processed IDs unless the user explicitly requests re-evaluation.
- Retrieve full content only when the workflow requires expense determination.
- Preserve a Gmail reference so the original message can be inspected during review.

### LLM decisions

- Analyze unlabeled message metadata for financial relevance.
- Determine whether each labeled or relevant message represents an expense.
- Extract the minimum transaction values only for confirmed expenses.
- Return `needs_review` when the decision or required values are uncertain.

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

No payment-application integration is required when the application sends receipts or transaction notifications into Gmail. Those messages enter the same inbox-wide workflow even when the sender is unknown and no filter exists.

Applications that send no email are outside the MVP and may require direct integrations later.

## Delivery stages

### Version 0 manual validation

Use Gmail MCP and an agent to process a sample of real inbox messages into a CSV ledger. Validate the two-stage decision flow and collect unresolved expense cases without building an application.

### Version 0.5 repeatable local workflow

Move the proven flow into a local tool that stores processed Gmail message IDs, decisions, review items, and confirmed expenses. The agent still retrieves Gmail messages through MCP and starts each run.

### Version 1 automatic personal service

Replace interactive retrieval with read-only Gmail API access and scheduled polling. Reuse the same decision, review, and ledger behavior rather than introducing a second workflow.

## Out of scope

The MVP does not require:

- Manual labeling of every financial sender.
- A complete definition of every financial event type before validation.
- Direct bank or payment-application connections.
- A dedicated forwarding address or inbound email provider.
- A dashboard or native mobile application.
- Multi-user accounts or public onboarding.
- Complex budgeting, subscription billing, or a custom-trained model.

## Definition of done

The MVP is complete when one agent request:

1. Finds new inbox messages whether or not they carry the `Finance Tracker` label.
2. Does not reprocess Gmail message IDs already recorded.
3. Uses the label only as a fast path to full expense determination.
4. Records each message as `expense`, `not_expense`, or `needs_review`.
5. Logs a traceable ledger row only for accepted expenses.
6. Presents uncertain decisions and missing transaction values for review.

The first validation run should include both labeled and unlabeled messages. Its unresolved cases will determine the later rules for what counts as an expense.
