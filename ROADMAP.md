# Mizu Kakeibo Delivery Roadmap

## Guiding idea

Every new Gmail inbox email is retrieved in full and sent through one expense analyser. Every successfully processed message receives an explicit decision, and only a confirmed expense creates a ledger entry.

The roadmap first proves classification and extraction on a fixed private dataset, then makes the same analyser repeatable and automatic. Metadata routing, Gmail labels, expense edge cases, reporting, dashboards, and broader financial features are deferred until evidence shows what is needed.

The proposed [inbox-to-expense contract](docs/inbox-expense-contract.md) is the single definition of the retrieval, normalization, analyser, validation, and persistence boundaries used below.

## Issue index

| ID | Issue | Type | Blocked by |
| --- | --- | --- | --- |
| V0-1 | Build the private full-message evaluation set | HITL | None |
| V0-2 | Evaluate the expense analyser | HITL | V0-1 |
| V0.5-1 | Build one repeatable local inbox run | AFK | V0-2 proceed gate |
| V0.5-2 | Connect and approve the agent-driven sync | HITL | V0.5-1 |
| V1-1 | Automate new inbox processing | HITL | V0.5-2 proceed gate |

# Milestone Version 0 Prove the Idea

## Version 0 outcome

Determine whether one normalized message-body analyser can reliably distinguish clear completed expenses from other email, extract the required transaction values, and identify uncertainty. Version 0 evaluates the model on fixed private examples rather than treating an interactive agent run as evidence of quality. Attachment analysis and permanent product rules for financial edge cases are deferred.

## Issue V0-1 Build the private full-message evaluation set

**Type:** HITL

**Status:** `needs-triage`

**Blocked by:** None - can start immediately

### What to build

Use Gmail MCP to collect a fixed private set of 20 to 50 representative inbox messages. Retrieve the full message content needed by the analyser and establish human ground truth before viewing model outputs. Keep the dataset and all real message content outside the repository.

### Acceptance criteria

- [ ] The private set contains between 20 and 50 real inbox messages.
- [ ] The set includes ordinary non-financial mail, confirmed expenses, financial non-expenses, unknown senders, and ambiguous examples.
- [ ] Every example retains its Gmail message ID privately as its source reference.
- [ ] Every example has a human-labelled decision of `expense`, `not_expense`, or `needs_review`.
- [ ] Every expected expense has human-labelled merchant, amount in minor units, currency, and transaction date.
- [ ] Ground truth is recorded before model outputs are reviewed.
- [ ] Unsupported content and missing required values are represented as review cases rather than guessed values.
- [ ] Attachment bytes are not fetched, stored, or sent to the analyser.
- [ ] No real email content, identifiers, credentials, or private evaluation data enters the repository.

## Issue V0-2 Evaluate the expense analyser

**Type:** HITL

**Status:** `needs-triage`

**Blocked by:** V0-1

### What to build

Run every example through the versioned `qwen2.5:14b` configuration on local Ollama. Score classification, transaction extraction, uncertainty handling, consistency, latency, and local resource usage. Finish with a `proceed`, `revise`, or `stop` decision for Version 0.5.

### Acceptance criteria

- [ ] Every evaluation example receives full-message analysis through the same input and output contract.
- [ ] Model output is limited to `expense`, `not_expense`, and `needs_review`.
- [ ] The report includes false expenses, missed expenses, correct review cases, unnecessary review cases, and per-field extraction accuracy.
- [ ] Merchant, amount, currency, and transaction date are scored separately and as an exact complete transaction.
- [ ] Invalid output, model failure, unsupported content, or missing required values cannot create an accepted expense.
- [ ] Policy-dependent financial events and attachment-only evidence return `needs_review`.
- [ ] Repeated runs measure decision and extraction consistency.
- [ ] Misclassifications are recorded as sanitized error categories without copying personal content into the repository.
- [ ] Model, model version, analyser contract version, prompt version, and runtime settings are recorded for reproducibility.
- [ ] The user records a `proceed`, `revise`, or `stop` decision with reasons.

## Version 0 completion gate

Version 0 is complete when both issues meet their acceptance criteria. Version 0.5 begins only after the evaluation records `proceed`.

# Milestone Version 0.5 Make It Repeatable

## Version 0.5 outcome

Move the proven full-message analyser into a small local tool. The agent still retrieves new Gmail messages through MCP, but the tool owns message-ID deduplication, analyser calls, review state, and the expense ledger.

## Version 0.5 stack

| Area | Choice |
| --- | --- |
| Runtime | Python with `pyproject.toml` |
| Gmail retrieval | Gmail MCP controlled by the agent |
| Expense analyser | Local Ollama with `qwen2.5:14b` and prompt `expense-v0.1` |
| Validation | Pydantic |
| Storage | SQLite with a unique Gmail message ID |
| Testing | Pytest with sanitized fixtures and mocked model responses |

## Issue V0.5-1 Build one repeatable local inbox run

**Type:** AFK

**Status:** `needs-triage`

**Blocked by:** V0-2 with a `proceed` decision

### What to build

Create one command that accepts a batch of normalized full Gmail messages from the agent and applies the entire workflow. It skips known message IDs, analyses every new message, stores every successful decision, logs confirmed expenses, and exposes uncertain results for review.

### Acceptance criteria

- [ ] One command accepts full message envelopes and processes them end to end.
- [ ] Known Gmail message IDs are skipped before any model call.
- [ ] Every new message receives one full-message analysis.
- [ ] Stored decisions are limited to `expense`, `not_expense`, and `needs_review`.
- [ ] Only accepted expenses create transactions, with amounts stored in integer minor units.
- [ ] Review items retain the model reason, missing fields, and Gmail message ID.
- [ ] The user can accept, reject, or correct a review item without changing the source email.
- [ ] An explicit re-evaluation can revisit a processed message without creating a duplicate transaction.
- [ ] Tests cover all three decisions, invalid model output, replay, and correction.
- [ ] Credentials, real email content, databases, and exports are excluded from version control.

## Issue V0.5-2 Connect and approve the agent-driven sync

**Type:** HITL

**Status:** `needs-triage`

**Blocked by:** V0.5-1

### What to build

Connect Gmail MCP retrieval to the local batch command and verify the workflow with real new inbox messages. One agent request should find new messages, retrieve each new message in full, pass them to the local tool, summarize the decisions, and present review items beside their source emails. Finish with a `proceed`, `revise`, or `stop` decision for Version 1.

### Acceptance criteria

- [ ] One agent request retrieves all new inbox messages in the selected batch.
- [ ] Every new message is retrieved in full before analysis.
- [ ] The result identifies expense, not-expense, review, skipped, and failed message IDs.
- [ ] Review items can be resolved while viewing the original Gmail message.
- [ ] An immediate replay makes no model calls for already processed Gmail message IDs.
- [ ] No credentials, real message contents, ledger data, or database files enter version control.
- [ ] The user records a `proceed`, `revise`, or `stop` decision with reasons.

## Version 0.5 completion gate

Version 0.5 is complete when both issues meet their acceptance criteria. Version 1 begins only after the real agent-driven sync records `proceed`.

# Milestone Version 1 Automate Inbox Processing

## Version 1 outcome

Remove the need for an interactive agent request. A scheduled personal service retrieves new inbox messages through read-only Gmail API access and passes them through the same proven analyser and ledger workflow.

## Issue V1-1 Automate new inbox processing

**Type:** HITL

**Status:** `needs-triage`

**Blocked by:** V0.5-2 with a `proceed` decision

### What to build

Add read-only Gmail authentication and scheduled polling for new inbox messages. The automated service reuses the Version 0.5 workflow unchanged: it skips processed IDs, retrieves every new message in full, analyses it, logs accepted transactions, and preserves uncertain cases for review.

### Acceptance criteria

- [ ] Gmail access uses the minimum read-only scope needed to retrieve inbox messages and their full content.
- [ ] OAuth credentials and tokens are stored outside the repository.
- [ ] A scheduled run retrieves and analyses new inbox messages.
- [ ] The automated path produces the same decisions and ledger records as the local batch command.
- [ ] Repeated or overlapping runs cannot process the same Gmail message ID twice.
- [ ] Failures are visible and retryable without duplicating successful work.
- [ ] A human verifies an expense, a non-expense, a review item, and the extracted transaction values.

## Version 1 completion gate

Version 1 is complete when scheduled processing works for real inbox messages without requiring Gmail labeling or an interactive agent request.

## Deferred decisions

The following work should be driven by evidence from the full-message evaluation rather than decided in advance:

- Whether a metadata-only gate can reduce full-message reads without hiding expenses.
- Whether a `Finance Tracker` label improves routing or provides a useful re-evaluation signal.
- Exact handling of refunds, reversals, transfers, pending charges, bills, and statements.
- Attachment retrieval and analysis.
- Categories, budgets, reporting, and visual dashboards.
- Direct bank or payment-application integrations for transactions that produce no email.
- Gmail push notifications instead of scheduled polling.
- Hosted deployment, PostgreSQL, multi-user accounts, and mobile applications.
