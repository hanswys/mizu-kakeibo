# Mizu Kakeibo Delivery Roadmap

## Document authority

This document is the source of truth for delivery sequencing: implementation issues, dependencies, milestone gates, and current status.

It does not redefine product scope or technical behavior. Use the [MVP](MVP.md) for product requirements and exclusions, and the [inbox-to-expense contract](inbox-expense-contract.md) for retrieval, normalization, analyser, validation, and persistence rules. A proposed roadmap item that changes either must update the authoritative document first.

The roadmap proves the analyser on a fixed private dataset, makes the workflow repeatable, and then automates it. Acceptance criteria below describe delivery evidence; where they summarize product behavior, the MVP and contract take precedence.

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

**Status:** `complete`

**Blocked by:** None - can start immediately

### What to build

Use Gmail MCP to collect a fixed private set of 20 to 50 representative inbox messages. Retrieve the full message content needed by the analyser and establish human ground truth before viewing model outputs. Keep the dataset and all real message content outside the repository.

### Acceptance criteria

- [x] The private set contains between 20 and 50 real inbox messages.
- [x] The set includes ordinary non-financial mail, confirmed expenses, financial non-expenses, unknown senders, and ambiguous examples.
- [x] Every example retains its Gmail message ID privately as its source reference.
- [x] Every example has a human-labelled decision of `expense`, `not_expense`, or `needs_review`.
- [x] Every expected expense has human-labelled merchant, amount in minor units, currency, and transaction date.
- [x] Ground truth is recorded before model outputs are reviewed.
- [x] Unsupported content and missing required values are represented as review cases rather than guessed values.
- [x] Attachment bytes are not fetched, stored, or sent to the analyser.
- [x] No real email content, identifiers, credentials, or private evaluation data enters the repository.

## Issue V0-2 Evaluate the expense analyser

**Type:** HITL

**Status:** `complete` (a 33-message private set was rebuilt through the claude.ai Gmail connector, using the `RAW` converter pulled forward from the job tracker's [J0-3](../jobs/ROADMAP.md#issue-j0-3-read-connector-messages-through-the-shared-normalizer), and evaluated on 2026-10-15; see the [evaluation note](v0-2-evaluation-2026-10-15.md). The user chose `revise`, then `proceed` on 2026-10-15 with prompt `expense-v0.6`, which made 33 of 33 correct decisions on the full set.)

**Blocked by:** V0-1

### What to build

Run every example through the versioned `qwen2.5:14b` configuration on local Ollama. Score classification, transaction extraction, uncertainty handling, consistency, latency, and local resource usage. Record each example's Gmail Purchases membership so a later decision on the deferred Purchases gate has evidence, and use the context-overflow count to decide how over-budget messages are handled. Finish with a `proceed`, `revise`, or `stop` decision for Version 0.5.

### Acceptance criteria

- [x] Every evaluation example receives full-message analysis through the same input and output contract.
- [x] Model output is limited to `expense`, `not_expense`, and `needs_review`.
- [x] The report includes false expenses, missed expenses, correct review cases, unnecessary review cases, and per-field extraction accuracy.
- [x] Merchant, amount, currency, and transaction date are scored separately and as an exact complete transaction.
- [x] Invalid output, model failure, unsupported content, or missing required values cannot create an accepted expense.
- [x] Policy-dependent financial events and attachment-only evidence return `needs_review`.
- [x] Repeated runs measure decision and extraction consistency.
- [x] Misclassifications are recorded as sanitized error categories without copying personal content into the repository.
- [x] Model, model version, analyser contract version, prompt version, and runtime settings are recorded for reproducibility.
- [x] The report counts expected expenses and review items inside and outside Gmail's Purchases category.
- [x] The user records a `proceed`, `revise`, or `stop` decision with reasons.

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
| Expense analyser | Local Ollama with `qwen2.5:14b`, using the versioned prompt and runtime settings in the [contract](inbox-expense-contract.md#4-model-runtime-and-prompt) |
| Validation | Pydantic |
| Storage | SQLite with a unique Gmail message ID |
| Testing | Pytest with sanitized fixtures and mocked model responses |

## Issue V0.5-1 Build one repeatable local inbox run

**Type:** AFK

**Status:** `implemented` (the V0-2 `proceed` gate was met on 2026-10-15; milestone acceptance is next)

**Blocked by:** V0-2 with a `proceed` decision

### What to build

Create one command that accepts a batch of normalized full Gmail messages from the agent and applies the entire workflow. It skips known message IDs, analyses every new message, stores every successful decision, logs confirmed expenses, and exposes uncertain results for review.

### Acceptance criteria

- [x] One command accepts full message envelopes and processes them end to end.
- [x] Known Gmail message IDs are skipped before any model call.
- [x] Every new message receives one full-message analysis.
- [x] Stored decisions are limited to `expense`, `not_expense`, and `needs_review`.
- [x] Only accepted expenses create transactions, with amounts stored in integer minor units.
- [x] Review items retain the sanitized model reason, missing fields, and Gmail message ID.
- [x] The user can accept, reject, or correct a review item without changing the source email.
- [x] An explicit re-evaluation can revisit a processed message without creating a duplicate transaction.
- [x] Tests cover all three decisions, invalid model output, replay, and correction.
- [x] Credentials, real email content, databases, and exports are excluded from version control.

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

## Scope changes

Deferred and excluded product work is maintained in the MVP rather than duplicated here. Add it to this roadmap only after the MVP scope changes, then express it as an issue with a dependency and completion gate.
