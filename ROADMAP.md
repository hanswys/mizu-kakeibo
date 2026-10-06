# Mizu Kakeibo Delivery Roadmap

## Guiding idea

Every new Gmail inbox email is eligible for analysis. The `Finance Tracker` label is an optional fast path, not an ingestion requirement and not proof of an expense. Every message is processed once, receives an explicit decision, and creates a ledger entry only when an expense is confirmed.

The roadmap stays focused on proving and then automating that flow. Expense edge cases, reporting, dashboards, and broader financial features are deferred until real messages show what is needed.

## Issue index

| ID | Issue | Type | Blocked by |
| --- | --- | --- | --- |
| V0-1 | Prove the inbox-to-expense flow | HITL | None |
| V0-2 | Validate the flow with real inbox messages | HITL | V0-1 |
| V0.5-1 | Build one repeatable local inbox run | AFK | V0-2 proceed gate |
| V0.5-2 | Connect and approve the agent-driven sync | HITL | V0.5-1 |
| V1-1 | Automate new inbox processing | HITL | V0.5-2 proceed gate |

# Milestone Version 0 Prove the Idea

## Version 0 outcome

Prove the workflow manually with Gmail MCP, an agent, and a private CSV file. The goal is to learn whether real inbox messages contain enough information to identify expenses, not to settle every accounting rule or build an application.

## Issue V0-1 Prove the inbox-to-expense flow

**Type:** HITL

**Status:** `needs-triage`

**Blocked by:** None - can start immediately

### What to build

Run the complete workflow on a small set containing both labeled and unlabeled Gmail messages. Unlabeled messages receive lightweight relevance analysis. Labeled and possibly financial messages receive full expense determination. Confirmed expenses are appended to a private CSV ledger, non-expenses are recorded as processed, and uncertain messages are presented for review.

### Acceptance criteria

- [ ] The input includes at least one unlabeled inbox message and one message carrying the optional `Finance Tracker` label.
- [ ] Unlabeled messages receive lightweight analysis using sender, subject, and snippet before any full-content inspection.
- [ ] Labeled messages bypass lightweight analysis but still receive expense determination.
- [ ] Every processed message ends as `expense`, `not_expense`, or `needs_review`.
- [ ] Only accepted expenses create CSV ledger rows.
- [ ] Every decision and transaction retains its Gmail message ID and source reference.
- [ ] Reprocessing the same Gmail message ID does not create another decision or ledger row.
- [ ] No real email content, credentials, or private ledger data enters the repository.

## Issue V0-2 Validate the flow with real inbox messages

**Type:** HITL

**Status:** `needs-triage`

**Blocked by:** V0-1

### What to build

Run the manual workflow against 20 to 50 real inbox messages that include obvious non-financial mail, possible financial mail, labeled mail, and unknown senders. Review every outcome and record the unresolved cases that require a future decision about what counts as an expense. Finish with a `proceed`, `revise`, or `stop` decision for Version 0.5.

### Acceptance criteria

- [ ] Between 20 and 50 real inbox messages are processed, including both labeled and unlabeled examples.
- [ ] Every message has exactly one recorded decision and no Gmail message ID is processed twice.
- [ ] Every accepted expense has the required transaction values and a Gmail source reference.
- [ ] Uncertain expense types and missing values remain review items rather than being silently classified.
- [ ] Misclassifications are recorded as sanitized error categories without copying personal content into the repository.
- [ ] The user records which cases need later policy decisions without requiring those decisions for this milestone.
- [ ] The user records a `proceed`, `revise`, or `stop` decision with reasons.

## Version 0 completion gate

Version 0 is complete when both issues meet their acceptance criteria. Version 0.5 begins only after the pilot records `proceed`.

# Milestone Version 0.5 Make It Repeatable

## Version 0.5 outcome

Move the proven decision flow into a small local tool. The agent still retrieves new Gmail messages through MCP, but the tool owns message-id deduplication, LLM decisions, review state, and the expense ledger.

## Version 0.5 stack

| Area | Choice |
| --- | --- |
| Runtime | Python with `pyproject.toml` |
| Gmail retrieval | Gmail MCP controlled by the agent |
| LLM decisions | OpenAI Responses API with structured output |
| Validation | Pydantic |
| Storage | SQLite with a unique Gmail message ID |
| Testing | Pytest with sanitized fixtures and mocked LLM responses |

## Issue V0.5-1 Build one repeatable local inbox run

**Type:** AFK

**Status:** `needs-triage`

**Blocked by:** V0-2 with a `proceed` decision

### What to build

Create one command that accepts a batch of Gmail message envelopes from the agent and applies the entire workflow. It skips known message IDs, uses the optional label to choose the fast path, performs relevance analysis and expense determination, stores every decision, logs confirmed expenses, and exposes uncertain results for review.

### Acceptance criteria

- [ ] One command accepts labeled and unlabeled message envelopes and processes them end to end.
- [ ] Known Gmail message IDs are skipped before any LLM call.
- [ ] Unlabeled messages receive lightweight relevance analysis; labeled messages go directly to expense determination.
- [ ] Stored decisions are limited to `expense`, `not_expense`, and `needs_review`.
- [ ] Only accepted expenses create transactions, with amounts stored in integer minor units.
- [ ] Review items retain the model reason, confidence, and Gmail source reference.
- [ ] The user can accept, reject, or correct a review item without changing the source email.
- [ ] An explicit re-evaluation can revisit a processed message without creating a duplicate transaction.
- [ ] Tests cover the label fast path, unlabeled discovery, all three decisions, replay, and correction.
- [ ] Credentials, real email content, databases, and exports are excluded from version control.

## Issue V0.5-2 Connect and approve the agent-driven sync

**Type:** HITL

**Status:** `needs-triage`

**Blocked by:** V0.5-1

### What to build

Connect Gmail MCP retrieval to the local batch command and verify the workflow with real new inbox messages. One agent request should find new messages regardless of label, pass them to the local tool, summarize the decisions, and present review items beside their source emails. Finish with a `proceed`, `revise`, or `stop` decision for Version 1.

### Acceptance criteria

- [ ] One agent request retrieves all new inbox messages, not only labeled messages.
- [ ] Labeled messages take the fast path and unlabeled financial messages can still be discovered.
- [ ] The result identifies expense, not-expense, review, skipped, and failed message IDs.
- [ ] Review items can be resolved while viewing the original Gmail message.
- [ ] An immediate replay makes no LLM calls for already processed message IDs.
- [ ] No credentials, real message contents, ledger data, or database files enter version control.
- [ ] The user records a `proceed`, `revise`, or `stop` decision with reasons.

## Version 0.5 completion gate

Version 0.5 is complete when both issues meet their acceptance criteria. Version 1 begins only after the real agent-driven sync records `proceed`.

# Milestone Version 1 Automate Inbox Processing

## Version 1 outcome

Remove the need for an interactive agent request. A scheduled personal service retrieves new inbox messages through read-only Gmail API access and passes them through the same proven decision and ledger workflow.

## Issue V1-1 Automate new inbox processing

**Type:** HITL

**Status:** `needs-triage`

**Blocked by:** V0.5-2 with a `proceed` decision

### What to build

Add read-only Gmail authentication and scheduled polling for new inbox messages. The automated service reuses the Version 0.5 workflow unchanged: it skips processed IDs, uses the optional label as a fast path, analyzes unlabeled messages, determines expenses, logs accepted transactions, and preserves uncertain cases for review.

### Acceptance criteria

- [ ] Gmail access uses the minimum read-only scope needed to retrieve inbox messages and label metadata.
- [ ] OAuth credentials and tokens are stored outside the repository.
- [ ] A scheduled run retrieves new labeled and unlabeled inbox messages.
- [ ] The automated path produces the same decisions and ledger records as the local batch command.
- [ ] Repeated or overlapping runs cannot process the same Gmail message ID twice.
- [ ] Failures are visible and retryable without duplicating successful work.
- [ ] A human verifies an unlabeled expense email, a labeled fast-path email, a non-expense, and a review item.

## Version 1 completion gate

Version 1 is complete when scheduled processing works for real inbox messages without requiring manual labeling or an interactive agent request.

## Deferred decisions

The following work should be driven by evidence from the validation messages rather than decided in advance:

- Exact handling of refunds, reversals, transfers, pending charges, bills, and statements.
- Categories, budgets, reporting, and visual dashboards.
- Direct bank or payment-application integrations for transactions that produce no email.
- Gmail push notifications instead of scheduled polling.
- Hosted deployment, PostgreSQL, multi-user accounts, and mobile applications.
