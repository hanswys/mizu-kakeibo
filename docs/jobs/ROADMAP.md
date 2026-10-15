# Job-Application Tracker Delivery Roadmap

## Document authority

This document is the source of truth for delivery sequencing: implementation issues, dependencies, milestone gates, and current status.

It does not redefine product scope or technical behavior. Use the [MVP](MVP.md) for product requirements and exclusions. The [job tracker contract](job-tracker-contract.md) records the sampled email layouts and, once J0-2 completes it, owns retrieval, analyser, matching, validation, and persistence rules. A proposed roadmap item that changes either must update the authoritative document first.

The roadmap samples real email layouts, settles the rules, tests parsing and matching on a fixed private dataset, and then builds the repeatable tool. Issue IDs use a `J` prefix to stay distinct from the [expense roadmap](../expenses/ROADMAP.md).

## Issue index

| ID | Issue | Type | Blocked by |
| --- | --- | --- | --- |
| J0-1 | Sample LinkedIn and Jobstreet email layouts | HITL | None |
| J0-2 | Settle update rules and write the contract | HITL | J0-1 |
| J0-3 | Read connector messages through the shared normalizer | AFK | J0-2 |
| J0-4 | Build the private evaluation set | HITL | J0-2 |
| J0-5 | Evaluate the status analyser and matching | HITL | J0-3, J0-4 |
| J0.5-1 | Build one repeatable local job-tracker run | AFK | J0-5 proceed gate |
| J0.5-2 | Connect and approve the agent-driven sync | HITL | J0.5-1 |

# Milestone Version 0 Prove the Idea

## Version 0 outcome

Determine whether an LLM analyser can reliably pick out job-application emails from the whole inbox and turn them into company, job title, and status, and whether matching on company and job title produces one correct application per role. This milestone is the dedicated testing time for the job tracker. Nothing is built for daily use until it records `proceed`.

## Issue J0-1 Sample LinkedIn and Jobstreet email layouts

**Type:** HITL

**Status:** `done` (2026-10-14; findings in the contract's [sampled layouts](job-tracker-contract.md#sampled-layouts))

**Blocked by:** None

### What to build

Use Gmail MCP read-only calls to find LinkedIn and Jobstreet emails received on or after 2026-04-14 and inspect their structure. Record the structure only: sender addresses and subject patterns as anonymized templates, MIME layout, and where the company, job title, and status appear.

### Acceptance criteria

- [x] Known LinkedIn and Jobstreet sender addresses are listed.
- [x] At least one email per sender and per observed status type is inspected, plus common non-application emails such as job alerts.
- [x] For each layout, the notes record whether company and job title are present and where.
- [x] The notes confirm whether the existing Gmail adapter and `inbox-message/v0` normalization handle these emails unchanged. They do not: the connector's response shape differs, and some LinkedIn layouts need the HTML alternative. J0-3 addresses both.
- [x] Notes contain field names, types, and layout only, with no real email content, names, or identifiers.

Sampling also showed that interview invitations and most outcomes come from employers rather than LinkedIn or Jobstreet, which led the MVP to consider every inbox message.

## Issue J0-2 Settle update rules and write the contract

**Type:** HITL

**Status:** `in-progress` (product decisions recorded in the MVP; contract sections not yet written)

**Blocked by:** J0-1

### What to build

Resolve the MVP's open questions using the sampled layouts, update the MVP, then complete [`job-tracker-contract.md`](job-tracker-contract.md). The contract defines inbox retrieval, analyser input and output schemas, prompt, model and runtime, company and job title normalization, likely-match and alias rules, status update rules, and persistence.

### Acceptance criteria

- [x] The user decides allowed status changes and handling of out-of-order, duplicate, and contradictory emails, and the MVP records the decision.
- [x] The user decides how near-duplicate company names and job titles are handled, and the MVP records the decision.
- [ ] The contract defines the analyser output with only `status_update`, `not_application`, and `needs_review`.
- [ ] The contract defines deterministic matching on normalized company and job title, the likely-match rule that sends an email to review, and confirmed aliases.
- [ ] The contract defines the status change rules, including reopening after a re-application.
- [ ] The contract references the expense contract's `inbox-message/v0` envelope instead of copying it.
- [ ] The contract defines a job-tracker database separate from the expense store.

## Issue J0-3 Read connector messages through the shared normalizer

**Type:** AFK

**Status:** `in-progress` (the `RAW` converter was delivered on 2026-10-15 as shared work for the expense app's V0-2; the HTML option remains. For the expense app's V0-2 revision, the shared normalizer now also uses HTML when the plain-text alternative is a short stub (see the expense contract's [normalization rules](../expenses/inbox-expense-contract.md#2-normalized-inbox-message-envelope)). LinkedIn stubs were not tested against that rule, and their footer may make them too long for it, so the option is still needed.)

**Blocked by:** J0-2

### What to build

The Gmail connector returns messages in a different shape from the one the shared normalizer was built for, and some LinkedIn notifications carry their content only in the HTML alternative (see the contract's [sampled layouts](job-tracker-contract.md#sampled-layouts)). Add a converter from the connector's `RAW` message to the MIME-part shape the normalizer reads, and an option that makes the normalizer prefer the HTML alternative. The job tracker turns the option on; the expense app does not.

This changes shared infrastructure. Update the expense contract's Gmail boundary and normalization sections in the same change.

### Acceptance criteria

- [x] A `RAW` connector message converts into the MIME-part shape and normalizes into a valid `inbox-message/v0` envelope, with headers, received time, and text preserved.
- [ ] With the HTML option on, the envelope for a stub-plain-text layout contains the job title and status wording from the HTML part.
- [ ] With the option off, normalization output is unchanged, and the expense tests pass without modification.
- [x] The expense contract describes the observed connector tools and response shapes.
- [ ] The expense contract describes the HTML option.
- [ ] Tests use sanitized synthetic fixtures only.

## Issue J0-4 Build the private evaluation set

**Type:** HITL

**Status:** `needs-triage`

**Blocked by:** J0-2

### What to build

Collect a fixed private set of real inbox emails and record human ground truth before viewing model output. Ground truth covers each email's decision and extracted values, and the expected final application list for the whole set.

### Acceptance criteria

- [ ] The set contains between 40 and 80 real emails from LinkedIn, Jobstreet, and employer or hiring-system senders.
- [ ] Every status appears at least once. `offered` may be covered by synthetic emails, clearly marked and kept separate in scoring.
- [ ] The set includes non-application emails, both job-related (alerts, recommendations, recruiter outreach) and unrelated to jobs.
- [ ] The set includes emails missing a company or job title, and at least three applications updated by several emails.
- [ ] The set includes an application whose company or job title is written differently across emails, and a re-application after a rejection.
- [ ] Where available, the set includes two roles at the same company and emails received out of status order.
- [ ] Every email has a human-labelled decision, plus company, job title, and status for each `status_update`.
- [ ] The expected final application list is recorded.
- [ ] Ground truth is recorded before model outputs are reviewed.
- [ ] No real email content, identifiers, or private evaluation data enters the repository.

## Issue J0-5 Evaluate the status analyser and matching

**Type:** HITL

**Status:** `needs-triage`

**Blocked by:** J0-3, J0-4

### What to build

Run every email through the contract's analyser configuration, then replay the validated results in received order through the matching rules. Score extraction per email and the resulting application list against ground truth. Finish with a `proceed`, `revise`, or `stop` decision for Version 0.5.

### Acceptance criteria

- [ ] Every email receives analysis through the same input and output contract.
- [ ] The report scores decision accuracy, plus company, job title, and status accuracy, separately and as an exact match.
- [ ] Emails missing a company or job title return `needs_review` and never create or update an application.
- [ ] Invalid output or model failure cannot create or update an application.
- [ ] The resulting application list is compared with the expected list, counting merged, split, missing, and wrong-status applications, and the number of likely-match review items.
- [ ] Repeated runs measure consistency.
- [ ] Errors are recorded as sanitized categories without personal content.
- [ ] Model, prompt, contract, and runtime versions are recorded for reproducibility.
- [ ] The user records a `proceed`, `revise`, or `stop` decision with reasons.

## Version 0 completion gate

Version 0 is complete when all five issues meet their acceptance criteria. Version 0.5 begins only after the evaluation records `proceed`.

# Milestone Version 0.5 Make It Repeatable

## Version 0.5 outcome

Move the proven analyser and matching into a small local tool alongside the expense code. The agent retrieves Gmail messages through MCP, and the tool owns message-ID deduplication, analyser calls, matching, review state, and the application list in its own private database.

## Issue J0.5-1 Build one repeatable local job-tracker run

**Type:** AFK

**Status:** `needs-triage`

**Blocked by:** J0-5 with a `proceed` decision

### What to build

Create one command that accepts a batch of full Gmail messages from the agent and applies the whole workflow. It reuses the existing Gmail normalization unchanged, skips known message IDs, analyses every new message, matches status updates to applications, and exposes review items.

### Acceptance criteria

- [ ] One command accepts full message envelopes and processes them end to end.
- [ ] Known Gmail message IDs are skipped before any model call.
- [ ] Stored decisions are limited to `status_update`, `not_application`, and `needs_review`.
- [ ] At most one application exists per normalized company and job title.
- [ ] Every application links to all of its source Gmail message IDs.
- [ ] The user can accept, correct, or reject a review item without changing the source email.
- [ ] The application list shows company, job title, current status, and latest update date.
- [ ] The job-tracker database is separate from the expense database, and expense behavior is unchanged.
- [ ] Tests use sanitized fixtures and mocked model responses, and cover all three decisions, missing company or title, multiple updates to one application, two roles at one company, out-of-order emails, re-application after rejection, contradictory outcomes, likely matches and aliases, invalid model output, replay, and review correction.
- [ ] Credentials, real email content, and databases are excluded from version control.

## Issue J0.5-2 Connect and approve the agent-driven sync

**Type:** HITL

**Status:** `needs-triage`

**Blocked by:** J0.5-1

### What to build

Connect Gmail MCP retrieval to the local command. Run the first sync over emails received on or after 2026-04-14, then verify an incremental run. Finish with a `proceed`, `revise`, or `stop` decision.

### Acceptance criteria

- [ ] The first run retrieves all inbox emails received on or after 2026-04-14, and none earlier.
- [ ] The result identifies status-update, not-application, review, skipped, and failed message IDs.
- [ ] The user checks the application list against their own knowledge of their applications.
- [ ] Review items can be resolved while viewing the original Gmail message.
- [ ] An immediate replay makes no model calls for already processed message IDs.
- [ ] No credentials, real message contents, or database files enter version control.
- [ ] The user records a `proceed`, `revise`, or `stop` decision with reasons.

## Version 0.5 completion gate

Version 0.5 is complete when both issues meet their acceptance criteria and the user records `proceed`.

## Scope changes

Scheduled automation and sharing ingest or processing state with the expense app are excluded by the MVP. Add them here only after the MVP scope changes, as issues with dependencies and completion gates.
