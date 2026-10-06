# Version 0.5 Repeatable Finance Email Workflow

## Milestone outcome

Build the repeatable local workflow defined in [MVP.md](MVP.md). One agent request should retrieve newly labeled Gmail messages, extract valid transactions into a durable personal ledger, skip messages already processed, route uncertain results to review, and produce basic reports and CSV exports.

This milestone keeps Gmail as the source of truth and the agent as the orchestrator. It does not introduce a hosted service, Gmail API integration, background processing, or a dashboard.

## User stories

- **US1:** As the user, I can process newly labeled financial emails with one agent request.
- **US2:** As the user, I receive structured ledger entries for clear financial emails.
- **US3:** As the user, I do not reprocess a Gmail message already recorded in the ledger.
- **US4:** As the user, I can review and correct uncertain extractions without changing the source email.
- **US5:** As the user, I can trace every proposal and transaction to its Gmail message.
- **US6:** As the user, I can inspect monthly spending, category totals, refunds, and the review queue.
- **US7:** As the user, I can export the ledger to CSV.

## Technical stack

| Area | Choice | Boundary |
| --- | --- | --- |
| Runtime | Python 3.13 or newer, packaged with `pyproject.toml` | Local execution only |
| Dependency management | `uv` with a committed lockfile | Do not add a service runtime |
| Command line | Standard-library `argparse` | One `mizu` entry point with focused subcommands |
| Gmail access | `Finance Tracker` label and Gmail MCP | The agent retrieves messages; application code does not call the Gmail API |
| Extraction | OpenAI Responses API with strict Structured Outputs | The model returns the transaction contract from `MVP.md` |
| Validation | Pydantic models | Reject malformed model output before ledger writes |
| Ledger | SQLite through Python's `sqlite3` module | `source_email_id` is unique; Gmail remains the original record |
| Reporting and export | SQL queries and Python's `csv` module | No analytics service or spreadsheet dependency |
| Testing | `pytest` with sanitized email fixtures and mocked model responses | Tests must not require Gmail or OpenAI credentials |
| Quality checks | Ruff | Lint and format Python code |
| Secrets and personal data | Environment variables plus ignored local data paths | Never commit API keys, OAuth material, real emails, CSV ledgers, or database files |

## Data boundaries

SQLite should separate source processing, extraction proposals, and accepted transactions:

- `source_messages` records the Gmail message ID, source reference, processing state, and timestamps without treating copied email content as the source of truth.
- `extraction_proposals` records model output, confidence, review reasons, and its source message.
- `transactions` contains accepted or corrected ledger entries and retains the proposal and Gmail message references.

The database must enforce uniqueness for `source_email_id`. Amounts use integer minor units, currencies use uppercase codes, and transaction dates use ISO 8601 calendar dates. A correction creates the current ledger value without changing the Gmail source reference.

## Issue 1 Ingest one financial email into the local ledger

**Type:** AFK

**Status:** `needs-triage`

**Blocked by:** None - can start immediately

**User stories covered:** US2, US5

### What to build

Create the smallest complete path from a source email envelope to a persisted transaction. A `mizu ingest` command accepts a Gmail message ID, source reference, subject, body, and supported attachment text; requests strict structured extraction; validates the response; and writes the source, proposal, and accepted transaction to SQLite. The command prints a concise result containing the transaction status and source reference.

The implementation establishes the Python package, database initialization, Pydantic transaction contract, OpenAI extraction adapter, CLI entry point, and test seams needed by later issues. Runtime secrets and personal data must stay outside version control.

### Acceptance criteria

- [ ] `mizu ingest` processes a sanitized clear-email fixture from input through validated extraction to a SQLite transaction.
- [ ] The Pydantic model represents every field in the transaction contract from `MVP.md` and rejects invalid amounts, currencies, dates, confidence values, and extra fields.
- [ ] The OpenAI adapter requests strict schema-conforming output and handles refusals and incomplete responses without writing an accepted transaction.
- [ ] The stored transaction retains links to both its extraction proposal and Gmail source reference.
- [ ] API keys, runtime databases, CSV exports, and real email fixtures are covered by repository ignore rules.
- [ ] Tests use mocked model responses and sanitized fixtures; they require no network access or credentials.
- [ ] Ruff and pytest commands are documented and pass.

## Issue 2 Process new emails idempotently

**Type:** AFK

**Status:** `needs-triage`

**Blocked by:** Issue 1

**User stories covered:** US1, US3, US5

### What to build

Extend the ingestion path to process a batch supplied by the agent. Before calling the model, the workflow checks each Gmail message ID against `source_messages` and skips IDs already recorded. It processes unseen messages independently, identifies possible transaction duplicates using a documented deterministic rule, and returns inserted, skipped, review, and failed counts.

### Acceptance criteria

- [ ] A batch containing new and known Gmail message IDs calls the extractor only for new IDs.
- [ ] Re-running the same batch creates no additional proposals or transactions.
- [ ] Concurrent or repeated attempts for one Gmail message ID cannot bypass the database uniqueness constraint.
- [ ] A documented deterministic comparison marks possible transaction duplicates for review without discarding either source message.
- [ ] One failed message does not silently lose or duplicate other batch results.
- [ ] The command reports inserted, skipped, review, and failed counts and identifies failures by source email ID.
- [ ] Tests cover a mixed batch, a complete replay, a possible duplicate, and a partial failure.

## Issue 3 Review and correct uncertain transactions

**Type:** AFK

**Status:** `needs-triage`

**Blocked by:** Issues 1 and 2

**User stories covered:** US4, US5

### What to build

Add a command-line review queue for proposals that cannot be accepted automatically. Missing required values, ambiguous dates, unknown currencies, possible duplicates, model refusals, and incomplete responses must produce an explicit review reason. The user can inspect a proposal with its Gmail source reference, accept it, correct it, or leave it pending.

### Acceptance criteria

- [ ] `mizu review list` displays pending proposals with their source email IDs and review reasons.
- [ ] `mizu review show` displays the proposed fields, confidence, review reasons, and Gmail source reference together.
- [ ] Clear proposals may be accepted automatically only when they pass schema and review-rule validation.
- [ ] The user can accept or correct a pending proposal into a valid transaction.
- [ ] Corrections preserve the original proposal and Gmail source reference.
- [ ] A possible duplicate can be accepted as distinct or linked to the existing transaction without deleting source history.
- [ ] Tests cover every required review reason and both acceptance and correction paths.

## Issue 4 Report and export the ledger

**Type:** AFK

**Status:** `needs-triage`

**Blocked by:** Issue 1

**User stories covered:** US6, US7

### What to build

Add read-only commands that summarize accepted ledger transactions and export them without exposing extraction internals. Reports cover current-month spending, spending by category, refunds, and pending-review counts. CSV export produces one row per accepted transaction with its Gmail source reference.

### Acceptance criteria

- [ ] The current-month report uses transaction dates and a documented local timezone boundary.
- [ ] Category totals use integer minor units and remain separated by currency.
- [ ] Refund totals are reported separately from expenses.
- [ ] The review report shows the number of pending proposals and can list their source IDs.
- [ ] CSV export contains one row per accepted transaction and includes `source_email_id` and the source reference.
- [ ] Reports and exports do not mutate ledger or review state.
- [ ] Tests cover multiple currencies, month boundaries, refunds, corrected transactions, and an empty ledger.

## Issue 5 Connect the Gmail MCP agent workflow

**Type:** HITL

**Status:** `needs-triage`

**Blocked by:** Issues 2 and 3

**User stories covered:** US1, US3, US4, US5

### What to build

Document and verify the agent workflow that bridges Gmail MCP and the local CLI. The agent searches the `Finance Tracker` label, retrieves full messages and supported attachments, creates the ingestion envelope expected by the CLI, submits unseen messages, and presents review items with their source emails. The workflow must be usable through one agent request while keeping credentials and real email content out of the repository.

### Acceptance criteria

- [ ] The workflow states the exact user request needed to run a sync.
- [ ] The agent searches only the intended Gmail label and passes the Gmail message ID as `source_email_id`.
- [ ] Full message bodies and supported attachment text are passed in the documented ingestion envelope.
- [ ] The workflow uses the batch result to distinguish inserted, skipped, pending-review, and failed messages.
- [ ] Review requests show the Gmail source email alongside the proposed transaction.
- [ ] A human verifies the workflow against at least one clear email and one email that requires review.
- [ ] No credentials, real message contents, ledger data, or database files are added to version control.

## Issue 6 Run the real-email validation pilot

**Type:** HITL

**Status:** `needs-triage`

**Blocked by:** Issues 3, 4, and 5

**User stories covered:** US1, US2, US3, US4, US5, US6, US7

### What to build

Run the completed workflow against 20 to 50 real financial emails labeled in Gmail. Review every result, record sanitized error categories, refine extraction instructions where a general rule is justified, and rerun affected fixtures. The pilot ends with a written decision on whether the workflow is useful enough to proceed to Version 1 automation.

### Acceptance criteria

- [ ] Between 20 and 50 real financial emails from the user's Gmail account are processed.
- [ ] Every email ends in exactly one recorded state: accepted transaction, pending review, skipped as already processed, or explicit failure.
- [ ] Re-running the pilot does not create duplicate records for the same Gmail message IDs.
- [ ] Every accepted or corrected transaction can be traced to its Gmail message.
- [ ] Current-month, category, refund, and review reports reconcile with the reviewed ledger.
- [ ] The ledger exports successfully to CSV.
- [ ] Extraction failures are recorded as sanitized categories without copying personal email content into the repository.
- [ ] The user records a proceed, revise, or stop decision for Version 1.

## Milestone completion criteria

The milestone is complete when all six issues meet their acceptance criteria and the pilot produces a proceed, revise, or stop decision. A successful completion proves the repeatable agent-driven workflow; it does not authorize or require a hosted service.

## Deferred Version 1 stack

Only a proceed decision from Issue 6 should open a Version 1 milestone. That milestone may replace Gmail MCP with Gmail API read-only OAuth and scheduled polling, add a FastAPI server-rendered review interface, and move from SQLite to PostgreSQL only if remote hosting or multi-user access requires it. Gmail push notifications, Cloud Pub/Sub, background workers, and a public application remain deferred until polling or local execution becomes inadequate.
