# Mizu Kakeibo Delivery Roadmap

## Roadmap outcome

Deliver the Gmail-to-ledger workflow in three gated milestones. Version 0 validates extraction against real financial emails without application code. Version 0.5 turns the proven workflow into a repeatable local tool. Version 1 automates Gmail retrieval and adds a private review dashboard.

Work may advance to the next milestone only after the current milestone's completion gate records a `proceed` decision. A `revise` decision creates follow-up work inside the current milestone, while a `stop` decision ends development without committing to later infrastructure.

## User stories

- **US1:** As the user, I can process newly labeled financial emails with one request or scheduled run.
- **US2:** As the user, I receive structured ledger entries for clear financial emails.
- **US3:** As the user, I do not reprocess a Gmail message already recorded in the ledger.
- **US4:** As the user, I can review and correct uncertain extractions without changing the source email.
- **US5:** As the user, I can trace every proposal and transaction to its Gmail message.
- **US6:** As the user, I can inspect monthly spending, category totals, refunds, and the review queue.
- **US7:** As the user, I can export the ledger to CSV.

## Issue index

| ID | Issue | Type | Blocked by |
| --- | --- | --- | --- |
| V0-1 | Prove one labeled email from Gmail to CSV | HITL | None |
| V0-2 | Run and assess the real-email pilot | HITL | V0-1 |
| V0.5-1 | Ingest one financial email into SQLite | AFK | V0-2 proceed gate |
| V0.5-2 | Process new emails idempotently | AFK | V0.5-1 |
| V0.5-3 | Review and correct uncertain transactions | AFK | V0.5-1, V0.5-2 |
| V0.5-4 | Report and export the local ledger | AFK | V0.5-1 |
| V0.5-5 | Connect the Gmail MCP agent workflow | HITL | V0.5-2, V0.5-3 |
| V0.5-6 | Approve the repeatable local workflow | HITL | V0.5-3, V0.5-4, V0.5-5 |
| V1-1 | Read one labeled message through the Gmail API | HITL | V0.5-6 proceed gate |
| V1-2 | Run an idempotent scheduled Gmail sync | HITL | V1-1 |
| V1-3 | Review an uncertain transaction in the private dashboard | AFK | V1-2 |
| V1-4 | Inspect reports and export from the dashboard | AFK | V1-3 |
| V1-5 | Complete the automated-service readiness review | HITL | V1-2, V1-3, V1-4 |

# Milestone Version 0 Validate the Manual Workflow

## Version 0 outcome

Prove that an agent can turn the user's real financial emails into an accurate, traceable CSV ledger before building application code. Gmail remains the inbox and source of truth, and the user runs the workflow interactively.

## Version 0 stack

| Area | Choice | Boundary |
| --- | --- | --- |
| Collection | Gmail filters and the `Finance Tracker` label | Configure known senders and label exceptions manually |
| Retrieval | Gmail MCP | Retrieve full bodies and supported attachments interactively |
| Extraction | Agent instructions and the JSON contract in [MVP.md](MVP.md) | Refine instructions from observed errors |
| Ledger | Private CSV file | One row per transaction with a Gmail source ID and reference |
| Review | Human review with the source email visible | Correct the CSV, never the email |
| Reporting | Agent or spreadsheet summaries over the CSV | Current month, categories, refunds, and review items only |

## Issue V0-1 Prove one labeled email from Gmail to CSV

**Type:** HITL

**Status:** `needs-triage`

**Blocked by:** None - can start immediately

**User stories covered:** US1, US2, US4, US5, US7

### What to build

Complete the smallest manual path through the whole workflow. Create the Gmail label and at least one useful filter, retrieve one labeled financial email through Gmail MCP, extract the transaction contract from `MVP.md`, review the result beside the source email, and append the accepted record to a private CSV ledger.

### Acceptance criteria

- [ ] The `Finance Tracker` label exists and at least one filter or manual labeling rule places a financial email under it.
- [ ] The agent retrieves the full source email and any supported attachment needed to understand the transaction.
- [ ] The extracted record contains every field from the transaction contract or is explicitly marked for review.
- [ ] The accepted CSV row stores `source_email_id` and a usable Gmail source reference.
- [ ] The user can compare the proposed transaction with the source email before acceptance.
- [ ] Reprocessing the same source email does not create a second accepted row.
- [ ] No real email content, credentials, or private ledger data is committed to the repository.

## Issue V0-2 Run and assess the real-email pilot

**Type:** HITL

**Status:** `needs-triage`

**Blocked by:** V0-1

**User stories covered:** US1, US2, US3, US4, US5, US6, US7

### What to build

Run the manual workflow against 20 to 50 real financial emails. Review every result, record sanitized error categories, refine extraction instructions only when a general rule is justified, and recalculate the basic reports from the corrected CSV. Finish with a `proceed`, `revise`, or `stop` decision for Version 0.5.

### Acceptance criteria

- [ ] Between 20 and 50 real financial emails are processed from the `Finance Tracker` label.
- [ ] Every email ends as an accepted transaction, a pending review item, a skipped source ID, or an explicit extraction failure.
- [ ] No Gmail message ID appears more than once among accepted ledger rows.
- [ ] Every accepted or corrected transaction retains its Gmail source reference.
- [ ] Current-month spending, category totals, refunds, and review items are produced from the corrected CSV.
- [ ] Extraction failures are recorded as sanitized categories without copying personal content into the repository.
- [ ] General extraction-instruction changes are rerun against the affected examples.
- [ ] The user records a `proceed`, `revise`, or `stop` decision with the reasons for that decision.

## Version 0 completion gate

Version 0 is complete when V0-1 and V0-2 meet their acceptance criteria. Version 0.5 may begin only after V0-2 records `proceed`, confirming that the real email formats are useful enough to justify a local implementation.

# Milestone Version 0.5 Build the Repeatable Local Workflow

## Version 0.5 outcome

Replace manual CSV mutation with a tested local command-line workflow. One agent request retrieves newly labeled Gmail messages, submits them to the local tool, skips processed source IDs, routes uncertain results to review, and updates a durable SQLite ledger.

Gmail MCP remains the retrieval mechanism and the agent remains the orchestrator. This milestone does not add Gmail API credentials, background scheduling, a hosted service, or a dashboard.

## Version 0.5 stack

| Area | Choice | Boundary |
| --- | --- | --- |
| Runtime | Python 3.13 or newer with `pyproject.toml` | Local execution only |
| Dependency management | `uv` with a committed lockfile | No service runtime |
| Command line | Standard-library `argparse` | One `mizu` entry point with focused subcommands |
| Gmail access | `Finance Tracker` label and Gmail MCP | The agent retrieves messages; application code does not call Gmail |
| Extraction | OpenAI Responses API with strict Structured Outputs | Return the transaction contract from `MVP.md` |
| Validation | Pydantic models | Reject malformed output before ledger writes |
| Ledger | SQLite through Python's `sqlite3` module | Enforce unique `source_email_id` values |
| Reporting and export | SQL queries and Python's `csv` module | No analytics service or spreadsheet dependency |
| Testing | `pytest` with sanitized fixtures and mocked model responses | Tests require no Gmail or OpenAI credentials |
| Quality checks | Ruff | Lint and format Python code |
| Secrets and personal data | Environment variables and ignored local data paths | Never commit keys, real emails, CSV ledgers, or databases |

## Version 0.5 data boundaries

SQLite separates source processing, extraction proposals, and accepted transactions:

- `source_messages` records the Gmail message ID, source reference, processing state, and timestamps without treating copied content as the source of truth.
- `extraction_proposals` records model output, confidence, review reasons, and its source message.
- `transactions` contains accepted or corrected ledger entries and retains the proposal and Gmail message references.

Amounts use integer minor units, currencies use uppercase codes, and transaction dates use ISO 8601 calendar dates. Corrections update the current ledger value without changing the original proposal or Gmail source reference.

## Issue V0.5-1 Ingest one financial email into SQLite

**Type:** AFK

**Status:** `needs-triage`

**Blocked by:** V0-2 with a `proceed` decision

**User stories covered:** US2, US5

### What to build

Create the smallest complete coded path from a source email envelope to a persisted transaction. A `mizu ingest` command accepts a Gmail message ID, source reference, subject, body, and supported attachment text; requests strict structured extraction; validates the response; and writes the source, proposal, and accepted transaction to SQLite.

### Acceptance criteria

- [ ] `mizu ingest` processes a sanitized clear-email fixture through validated extraction to a SQLite transaction.
- [ ] The Pydantic model represents every field in the `MVP.md` contract and rejects invalid values and extra fields.
- [ ] The OpenAI adapter requests strict schema output and handles refusals or incomplete responses without accepting a transaction.
- [ ] The stored transaction links to both its extraction proposal and Gmail source reference.
- [ ] Keys, runtime databases, CSV exports, and real email fixtures are covered by repository ignore rules.
- [ ] Tests use mocked model responses and sanitized fixtures without network access or credentials.
- [ ] Documented Ruff and pytest commands pass.

## Issue V0.5-2 Process new emails idempotently

**Type:** AFK

**Status:** `needs-triage`

**Blocked by:** V0.5-1

**User stories covered:** US1, US3, US5

### What to build

Process a batch supplied by the agent. Check each Gmail message ID before calling the model, skip IDs already recorded, process unseen messages independently, flag possible transaction duplicates using a documented deterministic rule, and return inserted, skipped, review, and failed counts.

### Acceptance criteria

- [ ] A mixed batch calls the extractor only for unseen Gmail message IDs.
- [ ] Replaying a batch creates no additional proposals or transactions.
- [ ] Concurrent or repeated attempts cannot bypass the unique source ID constraint.
- [ ] A documented comparison marks possible transaction duplicates for review without discarding either source.
- [ ] One failed message does not silently lose or duplicate other batch results.
- [ ] Results identify inserted, skipped, review, and failed source IDs.
- [ ] Tests cover a mixed batch, complete replay, possible duplicate, and partial failure.

## Issue V0.5-3 Review and correct uncertain transactions

**Type:** AFK

**Status:** `needs-triage`

**Blocked by:** V0.5-1 and V0.5-2

**User stories covered:** US4, US5

### What to build

Add a command-line review queue. Missing values, ambiguous dates, unknown currencies, possible duplicates, model refusals, and incomplete responses produce explicit review reasons. The user can inspect a proposal with its Gmail source reference, accept it, correct it, or leave it pending.

### Acceptance criteria

- [ ] `mizu review list` shows pending proposals with source IDs and review reasons.
- [ ] `mizu review show` displays proposed fields, confidence, reasons, and the Gmail source reference.
- [ ] Automatic acceptance requires both schema validation and review-rule validation.
- [ ] A pending proposal can be accepted or corrected into a valid transaction.
- [ ] Corrections preserve the original proposal and Gmail source reference.
- [ ] A possible duplicate can be accepted as distinct or linked to an existing transaction without deleting history.
- [ ] Tests cover every required review reason and both acceptance paths.

## Issue V0.5-4 Report and export the local ledger

**Type:** AFK

**Status:** `needs-triage`

**Blocked by:** V0.5-1

**User stories covered:** US6, US7

### What to build

Add read-only commands for current-month spending, category totals, refunds, pending-review counts, and CSV export. Reports operate on accepted ledger transactions, and exported rows retain their Gmail source references.

### Acceptance criteria

- [ ] Current-month reporting uses transaction dates and a documented local timezone boundary.
- [ ] Category totals use integer minor units and remain separated by currency.
- [ ] Refunds are reported separately from expenses.
- [ ] Review reporting shows pending counts and source IDs.
- [ ] CSV export contains one row per accepted transaction with its source ID and reference.
- [ ] Reporting and export do not mutate ledger or review state.
- [ ] Tests cover multiple currencies, month boundaries, refunds, corrections, and an empty ledger.

## Issue V0.5-5 Connect the Gmail MCP agent workflow

**Type:** HITL

**Status:** `needs-triage`

**Blocked by:** V0.5-2 and V0.5-3

**User stories covered:** US1, US3, US4, US5

### What to build

Document and verify the agent workflow bridging Gmail MCP and the local CLI. The agent searches the `Finance Tracker` label, retrieves full messages and supported attachments, creates the documented ingestion envelopes, submits unseen messages, and presents review items beside their source emails.

### Acceptance criteria

- [ ] The workflow states the exact user request needed to run a sync.
- [ ] The agent searches only the intended label and uses the Gmail message ID as `source_email_id`.
- [ ] Full bodies and supported attachment text use the documented ingestion envelope.
- [ ] The agent distinguishes inserted, skipped, review, and failed batch results.
- [ ] Review requests show the Gmail source email beside the proposed transaction.
- [ ] A human verifies one clear email and one email requiring review.
- [ ] No credentials, real message contents, ledger data, or database files enter version control.

## Issue V0.5-6 Approve the repeatable local workflow

**Type:** HITL

**Status:** `needs-triage`

**Blocked by:** V0.5-3, V0.5-4, and V0.5-5

**User stories covered:** US1, US2, US3, US4, US5, US6, US7

### What to build

Exercise the complete local workflow with new real emails and decide whether it should become an automated personal service. Run a normal sync, replay the same request, resolve review items, inspect reports, export the ledger, and record a `proceed`, `revise`, or `stop` decision for Version 1.

### Acceptance criteria

- [ ] One agent request processes all newly labeled messages through the local tool.
- [ ] An immediate replay skips every previously processed Gmail message ID.
- [ ] At least one review item can be resolved without changing its source email.
- [ ] Every transaction remains traceable to Gmail.
- [ ] Reports reconcile with the reviewed ledger and CSV export.
- [ ] Failures are explicit and can be retried without duplicating successful work.
- [ ] The user records a `proceed`, `revise`, or `stop` decision with reasons.

## Version 0.5 completion gate

Version 0.5 is complete when V0.5-1 through V0.5-6 meet their acceptance criteria. Version 1 may begin only after V0.5-6 records `proceed`, confirming that repeated manual runs are useful enough to justify authentication, scheduling, and a dashboard.

# Milestone Version 1 Automate the Personal Service

## Version 1 outcome

Replace interactive Gmail MCP retrieval with read-only Gmail API access and scheduled polling while reusing the validated extraction and ledger core. Add a private, server-rendered dashboard for review, reporting, and export. The first deployment remains a single-user local service.

## Version 1 stack

| Area | Choice | Boundary |
| --- | --- | --- |
| Gmail access | Gmail API with read-only OAuth | Use the `Finance Tracker` label and persist Gmail message IDs |
| Scheduling | `mizu sync` invoked by `launchd` on macOS | Poll on a schedule; do not add Pub/Sub yet |
| Processing | Existing Python, Pydantic, and OpenAI extraction core | No second extraction implementation |
| Service | FastAPI with Jinja2 server-rendered pages | Bind locally; no SPA or Node toolchain |
| Ledger | Existing SQLite database | Move to PostgreSQL only if later hosting or concurrency requires it |
| Testing | Pytest with fake Gmail responses, mocked model responses, and temporary databases | Live-account checks remain HITL |
| Credentials | OAuth and API tokens stored outside the repository in a protected user data directory | Request only the scopes needed for read-only retrieval |

## Issue V1-1 Read one labeled message through the Gmail API

**Type:** HITL

**Status:** `needs-triage`

**Blocked by:** V0.5-6 with a `proceed` decision

**User stories covered:** US1, US2, US5

### What to build

Add read-only Gmail OAuth and an API adapter that retrieves one message from the `Finance Tracker` label, including its full body and supported attachments, then passes the resulting source envelope through the existing ingestion path.

### Acceptance criteria

- [ ] OAuth requests the minimum read-only Gmail scope needed by the workflow.
- [ ] Tokens and client credentials are stored outside the repository in a protected user data directory.
- [ ] The adapter resolves the `Finance Tracker` label and retrieves a selected message's full content.
- [ ] The Gmail message ID and source reference are preserved through ingestion.
- [ ] Unit tests use fake Gmail responses and require no credentials.
- [ ] A human completes OAuth and verifies one real labeled message end to end.

## Issue V1-2 Run an idempotent scheduled Gmail sync

**Type:** HITL

**Status:** `needs-triage`

**Blocked by:** V1-1

**User stories covered:** US1, US2, US3, US5

### What to build

Add a `mizu sync` command that polls the Gmail label for messages, submits only unseen source IDs through the existing batch workflow, and exits with an observable summary. Provide a `launchd` configuration and installation instructions so the user can run the command automatically on macOS.

### Acceptance criteria

- [ ] `mizu sync` retrieves labeled messages and processes only unseen Gmail message IDs.
- [ ] Repeated and overlapping runs cannot create duplicate proposals or transactions.
- [ ] Partial Gmail or model failures are reported and can be retried safely.
- [ ] Logs identify counts and source IDs without recording message bodies or credentials.
- [ ] The `launchd` configuration uses explicit executable, working, data, and log paths.
- [ ] Tests cover no-op, new-message, replay, partial-failure, and concurrent-run behavior.
- [ ] A human verifies at least one scheduled run against the real Gmail account.

## Issue V1-3 Review an uncertain transaction in the private dashboard

**Type:** AFK

**Status:** `needs-triage`

**Blocked by:** V1-2

**User stories covered:** US4, US5

### What to build

Add a local FastAPI page that lists pending proposals and lets the user inspect, accept, correct, or defer one item. The page uses the existing review rules and database operations and keeps the Gmail source reference visible throughout the decision.

### Acceptance criteria

- [ ] The service binds to localhost by default and does not expose a public listener silently.
- [ ] The review list shows pending proposals and explicit review reasons.
- [ ] The detail page shows proposed values, confidence, and the Gmail source reference.
- [ ] Accept and correct actions use the same validation and history-preservation rules as the CLI.
- [ ] Defer leaves the proposal pending without mutation.
- [ ] Request and database tests cover list, detail, accept, correct, invalid input, and missing records.

## Issue V1-4 Inspect reports and export from the dashboard

**Type:** AFK

**Status:** `needs-triage`

**Blocked by:** V1-3

**User stories covered:** US6, US7

### What to build

Expose the existing ledger reports and CSV export through server-rendered dashboard pages. The web layer must reuse the same report queries as the CLI so both interfaces produce consistent totals and source references.

### Acceptance criteria

- [ ] The dashboard displays current-month totals, category totals, refunds, and pending-review counts.
- [ ] Amounts remain separated by currency and use the same timezone boundary as the CLI.
- [ ] CSV download matches the CLI export columns and accepted transaction rows.
- [ ] Corrected transactions appear once with their current accepted values.
- [ ] Empty and mixed-currency states render clearly.
- [ ] Tests prove CLI and dashboard reports use the same query results.

## Issue V1-5 Complete the automated-service readiness review

**Type:** HITL

**Status:** `needs-triage`

**Blocked by:** V1-2, V1-3, and V1-4

**User stories covered:** US1, US2, US3, US4, US5, US6, US7

### What to build

Verify the complete automated personal service in the user's environment. Confirm scheduled ingestion, restart and retry behavior, private review, report reconciliation, export, credential handling, and data recovery. Record whether Version 1 is accepted or needs targeted follow-up work.

### Acceptance criteria

- [ ] A scheduled run imports a new labeled message without an interactive agent request.
- [ ] Restarting the service and rerunning sync preserves ledger integrity and source-ID uniqueness.
- [ ] A real uncertain transaction can be reviewed through the dashboard and traced to Gmail.
- [ ] Dashboard and CLI reports reconcile with the accepted ledger.
- [ ] A CSV export can reconstruct the accepted ledger values and Gmail references.
- [ ] Credential, database, log, and backup locations are documented and excluded from version control.
- [ ] The user records an `accept` or `revise` decision with any required follow-up issues.

## Version 1 completion gate

Version 1 is complete when V1-1 through V1-5 meet their acceptance criteria and V1-5 records `accept`. Gmail push notifications, Cloud Pub/Sub, PostgreSQL, public hosting, multi-user accounts, native mobile applications, bank connections, complex budgeting, and subscription billing remain outside this roadmap until real usage demonstrates a need.
