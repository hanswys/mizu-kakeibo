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
| V0.5-3 | Skip promotions, social, and job alerts before analysis | AFK | V0.5-1 |
| V0.5-4 | Switch to a lighter analyser model | AFK | V0.5-1 |
| V0.5-5 | Correct a logged expense | AFK | V0.5-1 |
| V1-1 | Read new inbox messages through the Gmail API | AFK | V0.5-2 proceed gate |
| V1-2 | Lock processing runs and stop retrying failing messages | AFK | None |
| V1-3 | Schedule the daily sync on the laptop | HITL | V1-1, V1-2 |
| V1-4 | Report each scheduled sync and open the review list | AFK | V1-3 |
| V1-5 | Confirm the Gmail sign-in lasts past seven days | HITL | None |
| V1-6 | Verify scheduled processing on real inbox messages | HITL | V1-3, V1-4 |

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
| Gmail retrieval | The claude.ai Gmail connector, controlled by the agent through the `expense-sync` skill |
| Expense analyser | Local Ollama with `qwen3.5:4b-q4_K_M`, using the versioned prompt and runtime settings in the [contract](inbox-expense-contract.md#4-model-runtime-and-prompt) |
| Validation | Pydantic |
| Storage | SQLite with a unique Gmail message ID |
| Testing | Pytest with sanitized fixtures and mocked model responses |

## Issue V0.5-1 Build one repeatable local inbox run

**Type:** AFK

**Status:** `complete` (the V0-2 `proceed` gate was met on 2026-10-15)

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

**Status:** `complete` (the [connector sync](inbox-expense-contract.md#connector-sync) and the `expense-sync` skill are built; a 10-message trial on 2026-10-15 processed every message, skipped all of them on an immediate replay without model calls, and opened its Gmail link correctly. The trial found 466 inbox messages since 2026-10-01, so the user moved the start to 2026-10-10, which leaves about 140. The first full sync, on 2026-10-15, processed a 25-message batch at about a minute per message: one expense, 23 non-expenses, and one context overflow, with 93 deferred. Over-budget messages now go to review instead of failing on every sync. On 2026-10-16 the user resolved real review items beside their Gmail messages and moved the start to 2026-10-16, and a sync on the new model processed every new message with no failures. The user recorded `proceed` on 2026-10-16.)

**Blocked by:** V0.5-1

### What to build

Connect Gmail MCP retrieval to the local batch command and verify the workflow with real new inbox messages. One agent request should find new messages, retrieve each new message in full, pass them to the local tool, summarize the decisions, and present review items beside their source emails. Finish with a `proceed`, `revise`, or `stop` decision for Version 1.

### Acceptance criteria

- [x] One agent request retrieves all new inbox messages in the selected batch.
- [x] Every new message is retrieved in full before analysis.
- [x] The result identifies expense, not-expense, review, skipped, and failed message IDs.
- [x] Review items can be resolved while viewing the original Gmail message.
- [x] An immediate replay makes no model calls for already processed Gmail message IDs.
- [x] No credentials, real message contents, ledger data, or database files enter version control.
- [x] The user records a `proceed`, `revise`, or `stop` decision with reasons: `proceed` on 2026-10-16, because the sync works end to end and is fast enough.

## Issue V0.5-3 Skip promotions, social, and job alerts before analysis

**Type:** AFK

**Status:** `complete` (built on 2026-10-16 after the first full sync showed most analysed messages were promotions and job alerts; the next sync filtered four job alerts, and the user confirmed none was an expense)

**Blocked by:** V0.5-1

### What to build

Apply the [skip filter](inbox-expense-contract.md#skip-filter) in the connector sync so that messages in Gmail's Promotions and Social categories, and messages from job-alert senders, are never retrieved or analysed.

### Acceptance criteria

- [x] The sync search leaves out Promotions and Social.
- [x] Selection filters skipped-category labels and job-alert senders, and reports them as `filtered`.
- [x] Filtered messages are not recorded in the processed-message store.
- [x] Tests cover each filter rule, including a LinkedIn sender that is not a job alert.
- [x] The next real sync reports its filtered messages, and the user confirms none was an expense.

## Issue V0.5-4 Switch to a lighter analyser model

**Type:** AFK

**Status:** `complete` (adopted on 2026-10-16 after the user set speed and laptop load ahead of small accuracy gains; the next sync analysed three new messages on it, and the user confirmed its decisions)

**Blocked by:** V0.5-1

### What to build

Replace `qwen2.5:14b` on Ollama `0.23.1` with the smaller model, runtime, and `expense-v0.9` prompt in the [contract](inbox-expense-contract.md#4-model-runtime-and-prompt), and point the `expense-sync` server at the new runtime.

### Acceptance criteria

- [x] Candidate models are compared on the 33-message private set; the chosen model makes no false or missed expenses ([analyser model evaluation](analyser-model-2026-10-16.md)).
- [x] The analyser pins the new runtime, model digest, and prompt, turns thinking off, and releases the model soon after a sync.
- [x] The `expense-sync` server runs the new runtime.
- [x] The next real sync completes on the new model, and the user confirms its decisions.

## Issue V0.5-5 Correct a logged expense

**Type:** AFK

**Status:** `complete` (built on 2026-10-16 after a delivery-app receipt was logged under the restaurant's name instead of the app; the real ledger has not been corrected yet)

**Blocked by:** V0.5-1

### What to build

Add a command that applies a [ledger correction](inbox-expense-contract.md#ledger-corrections) to an expense that is already logged, and mention it in the `expense-sync` skill beside `review-resolve`. Until now only unresolved review items could be corrected.

### Acceptance criteria

- [x] The merchant, amount, currency, and transaction date of a logged expense can each be corrected without changing the source email.
- [x] The original analysis, its decision, and any review resolution are kept, and each correction is recorded.
- [x] The transaction and its ledger row are updated in place, keeping one row per Gmail message ID.
- [x] Repeating a correction records nothing new and leaves the ledger unchanged.
- [x] A source with no logged expense is rejected.
- [x] Tests cover each field, a source with no logged expense, a repeated correction, and the kept original decision, using sanitized fixtures.

## Version 0.5 completion gate

Version 0.5 was complete when V0.5-1 to V0.5-4 met their acceptance criteria. V0.5-5 was added afterwards from real use and does not block Version 1. Version 1 begins only after the real agent-driven sync records `proceed`.

# Milestone Version 1 Automate Inbox Processing

## Version 1 outcome

Remove the need for an interactive agent request. A scheduled personal service retrieves new inbox messages through read-only Gmail API access and passes them through the same proven analyser and ledger workflow.

## Issue V1-1 Read new inbox messages through the Gmail API

**Type:** AFK

**Status:** `complete` (built on 2026-10-16 as `gmail-sign-in` and `sync`, and tested with mocked Gmail responses. The first live `sync`, run by hand on 2026-10-17 with the existing sign-in, processed 10 new messages as non-expenses, skipped 7 already processed by the agent-driven sync, and filtered 20 job alerts, with no failures or deferred messages. A check of each sender and subject found every decision correct, and every filtered sender was a job board. Triaged earlier that day: the sign-in lifetime check moved to [V1-5](#issue-v1-5-confirm-the-gmail-sign-in-lasts-past-seven-days) and the human check to [V1-6](#issue-v1-6-verify-scheduled-processing-on-real-inbox-messages). A [Gmail API spike](gmail-api-spike-2026-10-16.md) the same day confirmed the scope and showed that search, `raw`, and envelopes match the connector.)

**Blocked by:** V0.5-2 with a `proceed` decision

### What to build

Add [Gmail API retrieval](inbox-expense-contract.md#gmail-api-retrieval) of new inbox messages and its sign-in command, so one command runs a sync with no agent request. [V1-3](#issue-v1-3-schedule-the-daily-sync-on-the-laptop) runs it on a schedule. The command reuses the Version 0.5 workflow unchanged. It applies the skip filter and the 25-message batch, skips processed IDs, retrieves every new message in full, analyses it, logs accepted transactions, and keeps uncertain and oversized cases for review.

### Acceptance criteria

- [x] Gmail access uses only `gmail.readonly`, the minimum read-only scope that retrieves inbox messages and their full content.
- [x] The sign-in command writes the OAuth token outside the repository, with the file modes in the contract. A sync never opens a browser.
- [x] An expired or removed sign-in stops the sync before retrieval with a sign-in error that is not counted against any message.
- [x] `snippet` never reaches a run file or log.
- [x] One command retrieves and analyses new inbox messages without an agent.
- [x] Selection, the skip filter, the 25-message batch, and oversized-message review match the connector sync, and the command reports the same seven `process` groups.
- [x] The automated path produces the same decisions and ledger records as the local batch command.
- [x] Tests use mocked API responses and sanitized fixtures to cover search paging, a `From` header with a display name, `snippet` removal, an expired sign-in, and a rate-limit error that leaves the message retryable.

## Issue V1-2 Lock processing runs and stop retrying failing messages

**Type:** AFK

**Status:** `complete` (designed and built on 2026-10-16 from the [scheduler spike](scheduler-spike-2026-10-16.md). The Gmail API `sync` from [V1-1](#issue-v1-1-read-new-inbox-messages-through-the-gmail-api) takes the same lock, and its retrieval errors are not counted against a message.)

**Blocked by:** None - can start immediately

### What to build

Add the [run lock](inbox-expense-contract.md#run-lock) to every processing command and the agent-driven sync, and send a message to review after [repeated failures](inbox-expense-contract.md#repeated-failures).

### Acceptance criteria

- [x] A sync, `process`, or re-evaluation that finds the lock held records nothing and exits successfully.
- [x] A run killed while holding the lock leaves nothing that blocks the next run.
- [x] Review resolution and ledger correction work while a sync holds the lock.
- [x] A message's third counted failure stores it as `needs_review`; failures that are not the message's own are not counted.
- [x] Tests cover overlapping runs in separate processes, a new database opened by two runs, and each failure kind.

## Issue V1-3 Schedule the daily sync on the laptop

**Type:** HITL

**Status:** `complete` (triaged and built on 2026-10-17 as `scheduled-sync` and `launch-agent`, and tested with mocked Gmail, runtime, and macOS state. The user installed the LaunchAgent that day, and its first scheduled run, at 14:00 in a full wake on mains power, searched the live inbox through the Gmail API with the runtime already up. It skipped 15 messages already processed by a manual `sync` earlier that hour and filtered 19, so no message was analysed. It exited successfully, marked the day done, and wrote a run record and log line with message IDs and counts only. The user accepted this as complete, because analysis is the same code as the manual `sync`, and [V1-6](#issue-v1-6-verify-scheduled-processing-on-real-inbox-messages) checks scheduled analysis on real messages. The user chose to run the schedule from a `uv tool install` copy of `main`, updated on purpose, rather than from a checkout, and confirmed that only a scheduled run marks the day done, so a manual `sync` does not stop the next scheduled hour. The design is otherwise the contract's.)

**Blocked by:** V1-1, V1-2

### What to build

Install a LaunchAgent that runs the V1-1 sync every hour within the [scheduled run](inbox-expense-contract.md#9-scheduled-run) window, behind its gates and runtime check, and writes its run record and log. The run record carries what [V1-4](#issue-v1-4-report-each-scheduled-sync-and-open-the-review-list) needs to decide on its notification and dialog.

### Acceptance criteria

- [x] A run stopped by the lock or a gate records its reason and exits successfully.
- [x] A run in a maintenance wake or with the lid closed does not sync, and the next full wake within the window does.
- [x] A run starts Ollama when it is down and fails before retrieval when the version is wrong.
- [x] A failed or deferred day is continued in the next hour, and a done day is not run again. A run after one with failed messages searches from the sync start, so they are retried.
- [x] Run records and log lines hold no message content and are deleted after 90 days.
- [x] The user installs the LaunchAgent, and a scheduled sync runs on real inbox messages.

## Issue V1-4 Report each scheduled sync and open the review list

**Type:** AFK

**Status:** `complete` (triaged and built on 2026-10-17, and tested with mocked macOS commands. A [follow-up spike](scheduler-spike-2026-10-16.md#dialogs-after-the-run-exits-2026-10-17) found that a dialog left as an ordinary child of the run is killed when the run exits, and one the run waits for would hold the next hour, so the dialog runs in a new session and the LaunchAgent is unchanged. The user chose that a run failing before processing shows only its dialog, at most once a day, with no notification; that every sync, `process`, and review resolution rewrites the review page, while only a scheduled run notifies; and the notification and dialog wording now in the [contract](inbox-expense-contract.md#9-scheduled-run). After the user installed it, a launchd run started by hand at 01:38 on 2026-10-18 analysed an expense and a non-expense, recorded a notification and no dialog, and wrote the review page. Because it had no review item, the review dialog was then shown from the installed copy without a sync, and the user opened the page from it. The user accepted V1-4 as complete; [V1-6](#issue-v1-6-verify-scheduled-processing-on-real-inbox-messages) checks real notifications and dialogs.)

**Blocked by:** V1-3

### What to build

Show the [notification and dialog](inbox-expense-contract.md#9-scheduled-run) after a scheduled sync, and keep the private review page current.

### Acceptance criteria

- [x] A scheduled sync that processed or failed a message notifies its counts; a sync with nothing new, and a run stopped by the lock or a gate, is silent.
- [x] Review items or failures also show a dialog whose `Open review list` button opens the review page.
- [x] The dialog is shown after the lock is released and does not hold the next run, and a run that fails before processing shows its failure dialog at most once a day, with no notification.
- [x] The review page is rewritten after each sync, `process`, and review resolution, with escaped values and no message content.
- [x] The user sees a real notification and dialog after a scheduled sync and opens the review page from the dialog.

## Issue V1-5 Confirm the Gmail sign-in lasts past seven days

**Type:** HITL

**Status:** `in-progress` (the Google Cloud project was moved to In production on 2026-10-16, and a new `gmail.readonly` token refreshed at once; the check is due on or after 2026-10-24. The user chose on 2026-10-18 to let the hourly sync make the check: every scheduled sync refreshes the token, so the first scheduled sync on or after 2026-10-24 that passes the sign-in is the evidence, and [V1-6](#issue-v1-6-verify-scheduled-processing-on-real-inbox-messages) runs meanwhile. Until then, do not run `gmail-sign-in --replace`, which would issue a new token and restart its age.)

**Blocked by:** None - can start immediately

### What to build

Check that a refresh token issued with the In production, unverified status keeps refreshing after the seven days a Testing token lasts, so that the [scheduled sync](MVP.md#scheduled-sync) does not need a weekly sign-in. The sign-in is shared with the job tracker, so the result applies to both projects.

### Acceptance criteria

- [ ] A token issued after the move to In production refreshes successfully more than seven days after it was issued.
- [ ] The check records only the token's age and whether it refreshed, with no message content or token values.
- [ ] If the refresh fails, the user decides how the sign-in is renewed, and the MVP and contract are updated before V1-6 is complete.

## Issue V1-6 Verify scheduled processing on real inbox messages

**Type:** HITL

**Status:** `in-progress` (triaged on 2026-10-18. The user chose to end the check when every item below has been seen over at least three days of scheduled syncs, instead of after a fixed week, because the done-today gate leaves only one or two syncs a day and most items can be set up on purpose. It started on 2026-10-18 alongside [V1-5](#issue-v1-5-confirm-the-gmail-sign-in-lasts-past-seven-days), which must pass before V1-6 is complete. Progress on 2026-10-18: the 01:38 scheduled run logged an expense and a non-expense whose decisions and values were checked against each message's sender, subject, and receipt total, pending the user's confirmation. An agent-driven `/expense-sync` that night searched and selected correctly and found nothing new, and its `process` step reported `locked` and recorded nothing while a stand-in process held the run lock; an overlap with a real scheduled sync is still to be seen.)

**Blocked by:** V1-3, V1-4; completion also needs V1-5

### What to build

Let the installed schedule run by itself on real inbox messages, then have the user check its results through the notification, dialog, and review page rather than a chat.

### Acceptance criteria

- [ ] Scheduled syncs run on real inbox messages on at least three days with no agent request, and until every item below has been seen.
- [ ] A human verifies an expense, a non-expense, a review item, and the extracted transaction values.
- [ ] The user resolves a review item from the review page beside its Gmail message.
- [ ] A day with the laptop closed or asleep for at least one window hour is caught up at the next full wake.
- [ ] The agent-driven `/expense-sync` still works as a fallback during the check, and it exits without doing anything while a scheduled sync holds the lock.
- [ ] The user records whether Version 1 is complete, with reasons, or what must be revised.

## Version 1 completion gate

Version 1 is complete when V1-1 to V1-6 meet their acceptance criteria, so scheduled processing works for real inbox messages without requiring Gmail labeling or an interactive agent request.

## Scope changes

Deferred and excluded product work is maintained in the MVP rather than duplicated here. Add it to this roadmap only after the MVP scope changes, then express it as an issue with a dependency and completion gate.
