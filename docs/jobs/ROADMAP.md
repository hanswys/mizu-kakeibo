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
| J0.5-2 | Retrieve job messages through the Gmail API | AFK | J0.5-1 |
| J0.5-3 | First sync and approval | HITL | J0.5-2 |

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

**Status:** `complete` (2026-10-17; the [contract](job-tracker-contract.md) defines retrieval, the analyser on the expense app's pinned model, normalization, matching, status changes, review, persistence, and evaluation)

**Blocked by:** J0-1

### What to build

Resolve the MVP's open questions using the sampled layouts, update the MVP, then complete [`job-tracker-contract.md`](job-tracker-contract.md). The contract defines inbox retrieval, analyser input and output schemas, prompt, model and runtime, company and job title normalization, likely-match and alias rules, status update rules, and persistence.

### Acceptance criteria

- [x] The user decides allowed status changes and handling of out-of-order, duplicate, and contradictory emails, and the MVP records the decision.
- [x] The user decides how near-duplicate company names and job titles are handled, and the MVP records the decision.
- [x] The contract defines the analyser output with only `status_update`, `not_application`, and `needs_review`.
- [x] The contract defines deterministic matching on normalized company and job title, the likely-match rule that sends an email to review, and confirmed aliases.
- [x] The contract defines the status change rules, including reopening after a re-application.
- [x] The contract references the expense contract's `inbox-message/v0` envelope instead of copying it.
- [x] The contract defines a job-tracker database separate from the expense store.

## Issue J0-3 Read connector messages through the shared normalizer

**Type:** AFK

**Status:** `complete` (2026-10-17. The `RAW` converter was delivered on 2026-10-15 as shared work for the expense app's V0-2, and the prefer-HTML option on 2026-10-17; see the expense contract's [normalization rules](../expenses/inbox-expense-contract.md#2-normalized-inbox-message-envelope). The option does not depend on the stub rule, which LinkedIn stubs with a long footer can defeat.)

**Blocked by:** J0-2

### What to build

The Gmail connector returns messages in a different shape from the one the shared normalizer was built for, and some LinkedIn notifications carry their content only in the HTML alternative (see the contract's [sampled layouts](job-tracker-contract.md#sampled-layouts)). Add a converter from the connector's `RAW` message to the MIME-part shape the normalizer reads, and an option that makes the normalizer prefer the HTML alternative. The job tracker turns the option on; the expense app does not.

This changes shared infrastructure. Update the expense contract's Gmail boundary and normalization sections in the same change.

### Acceptance criteria

- [x] A `RAW` connector message converts into the MIME-part shape and normalizes into a valid `inbox-message/v0` envelope, with headers, received time, and text preserved.
- [x] With the HTML option on, the envelope for a stub-plain-text layout contains the job title and status wording from the HTML part.
- [x] With the option off, normalization output is unchanged, and the expense tests pass without modification.
- [x] The expense contract describes the observed connector tools and response shapes.
- [x] The expense contract describes the HTML option.
- [x] Tests use sanitized synthetic fixtures only.

## Issue J0-4 Build the private evaluation set

**Type:** HITL

**Status:** `complete` (2026-10-18. Labels confirmed by the user and assembled in the contract's [private dataset format](job-tracker-contract.md#private-dataset-format), outside the repository.)

**Blocked by:** J0-2

### What to build

Collect a fixed private set of real inbox emails and record human ground truth before viewing model output. Ground truth covers each email's decision and extracted values, and the expected final application list for the whole set.

### Acceptance criteria

- [x] The set contains between 40 and 80 real emails from LinkedIn, Jobstreet, and employer or hiring-system senders. It has 70 real emails, all received on or after 2026-04-14: 18 LinkedIn, 20 Jobstreet, 28 employer or hiring-system, and 4 unrelated senders.
- [x] Every status appears at least once. `offered` may be covered by synthetic emails, clearly marked and kept separate in scoring. Real `status_update` labels: 23 `applied`, 6 `viewed`, 4 `interview`, 13 `rejected`. 4 synthetic emails add 3 `offered` and 1 `rejected`, including one contradictory offer after a rejection.
- [x] The set includes non-application emails, both job-related (alerts, recommendations, recruiter outreach) and unrelated to jobs. Real decisions: 46 `status_update`, 17 `not_application` (13 job-related, 4 unrelated), 7 `needs_review`.
- [x] The set includes emails missing a company or job title, and at least three applications updated by several emails. 6 of the 7 `needs_review` emails lack a job title, and 2 lack a status. 12 applications have two or more emails.
- [x] The set includes an application whose company or job title is written differently across emails, and a re-application after a rejection. 2 applications are written differently across emails, and 1 is re-applied for after a rejection.
- [x] Where available, the set includes two roles at the same company and emails received out of status order. 3 companies have two or more roles. No real email received out of status order was found in the window.
- [x] Every email has a human-labelled decision, plus company, job title, and status for each `status_update`.
- [x] The expected final application list is recorded. Real: 26 applications, 9 `applied`, 3 `viewed`, 2 `interview`, 12 `rejected`. Synthetic: 3 applications, 2 `offered`, 1 `rejected`.
- [x] Ground truth is recorded before model outputs are reviewed.
- [x] No real email content, identifiers, or private evaluation data enters the repository.

## Issue J0-5 Evaluate the status analyser and matching

**Type:** HITL

**Status:** `complete` (2026-10-18. The evaluator, `mizu-kakeibo jobs-evaluate` in the `mizu_kakeibo.jobs` package, ran three times on J0-4's private dataset with prompt `jobs-v0.1`, and the user chose `revise`. After prompt `jobs-v0.2` and the contract's [missing-value guard](job-tracker-contract.md#missing-value-guard), a second three-run evaluation on the unchanged dataset led the user to choose `proceed`; see [the decisions below](#j0-5-decision).)

**Blocked by:** J0-3, J0-4

### What to build

Run every email through the contract's analyser configuration, then replay the validated results in received order through the matching rules. Score extraction per email and the resulting application list against ground truth. Finish with a `proceed`, `revise`, or `stop` decision for Version 0.5.

### Acceptance criteria

- [x] Every email receives analysis through the same input and output contract.
- [x] The report scores decision accuracy, plus company, job title, and status accuracy, separately and as an exact match.
- [x] Emails missing a company or job title return `needs_review` and never create or update an application. With `jobs-v0.1`, only 2 of the 6 returned `needs_review` and the other 4 were invalid outputs. With `jobs-v0.2`, all 6 returned `needs_review`, 4 of them through the missing-value guard, and none created or updated an application.
- [x] Invalid output or model failure cannot create or update an application.
- [x] The resulting application list is compared with the expected list, counting merged, split, missing, and wrong-status applications, and the number of likely-match review items.
- [x] Repeated runs measure consistency.
- [x] Errors are recorded as sanitized categories without personal content.
- [x] Model, prompt, contract, and runtime versions are recorded for reproducibility.
- [x] The user records a `proceed`, `revise`, or `stop` decision with reasons: `revise` on 2026-10-18, because non-application emails created applications, then `proceed` the same day after the second run; see [the decisions](#j0-5-decision).

### J0-5 decision

`revise`, recorded by the user on 2026-10-18 after one three-run evaluation of 70 real and 4 synthetic emails. The configuration was `qwen3.5:4b-q4_K_M` on Ollama 0.40.5, prompt `jobs-v0.1`, contract `jobs-v0`, at temperature 0 with seed 42. All three runs were identical: 70 of 70 real decisions and outputs were consistent, and the replayed application lists matched. Median latency was 6.6 s per email, p95 10.7 s, and the model used 3.2 GiB, all on the GPU.

What works:

- Every one of the 46 real status updates was detected, with 46 correct companies, 40 correct job titles, 45 correct statuses, and 39 exact matches.
- No invalid output or failure created or updated an application, and there were no operational failures or context overflows.
- The 4 synthetic emails were all correct, and the contradictory offer after a rejection went to review with the status unchanged.

What must change before Version 0.5, from the real set:

- **False status updates.** 4 non-applications or review cases were reported as status updates, which created 4 extra applications: a bank application, an interview survey, a digest of several applications, and a canceled recruitment.
- **Missing job titles.** 4 of the 6 emails lacking a job title were invalid outputs. Three were status updates with an empty title and the field listed as missing, and one was `not_application` with missing fields, as in the smoke run.
- **Extra title text.** Job titles kept extra text that the contract excludes: requisition IDs and location suffixes. On one multi-email application this sent the later updates to likely-match review, leaving the application at its first status.
- **Status wording.** One hiring-system "reviewing" email was read as `viewed`.

Real decisions were 60 of 70 correct. Of the 26 expected applications, 22 were correct and 2 had the wrong status. There were 4 extra, 1 split, 1 missing, and 0 merged. Review items were 4 analyser and 4 likely-match. 2 grounding-check replacements turned unsupported titles into review items. The split application is the name variant that needs a confirmed alias, and the missing one is the containment trap held for likely-match review. Both follow the [matching rules](job-tracker-contract.md#6-matching).

Next: revise the prompt once for these error categories, version it, and rerun the same dataset unchanged. The user will consider `proceed` if the rerun has no false status updates, no invalid outputs on emails missing a job title, at least 44 of 46 correct job titles, and the multi-email application held back by extra title text at its expected status. The name-variant split and the likely-match review follow the matching rules and do not block `proceed`.

#### Second run: `proceed`

`proceed`, recorded by the user on 2026-10-18 after a second three-run evaluation of the same 70 real and 4 synthetic emails, unchanged. The configuration differed only in prompt `jobs-v0.2` and the new [missing-value guard](job-tracker-contract.md#missing-value-guard), which sends an output that reports a missing value to review instead of counting it as invalid. All three runs were identical again. Median latency was 7.1 s per email, p95 12.0 s. Prompts had a median of 1,197 tokens and a maximum of 2,044, within the 12,288 context.

Against the bar:

- **False status updates:** 0, down from 4.
- **Invalid outputs on emails missing a job title:** 0, down from 4. All 6 such emails returned `needs_review`.
- **Job titles:** 44 of 46 correct, up from 40. One title kept a location in brackets, and one status update was missed.
- **The multi-email application held back by extra title text:** it ended at its expected status.
- **No new errors among the 46 status updates:** not met. One rejection, the only email of its application, was read as `not_application`, because it also mentioned other roles and the new rule for emails listing several applications caught it. That application is missing from the list. Narrowing the rule sent the email to review instead and broke a company name on another email, so the evaluated prompt was kept.

Real decisions were 67 of 70 correct, up from 60. Of the 26 expected applications, 23 were correct and none had the wrong status. There were 0 extra, 0 merged, 1 split, and 2 missing: the name-variant split and the containment trap from the first run, which follow the matching rules, and the missed rejection. Review items were 9 analyser and 2 likely-match. 2 non-application emails went to review unnecessarily: a recruiter email and an unfinished-application reminder that the guard sent to review because it listed a missing status. There were no grounding replacements, invalid outputs, operational failures, or context overflows. The 4 synthetic emails were all correct again.

The user chose `proceed` with the one missed rejection, because the other four conditions were met, every other measure improved, and further wording changes on the 4B model traded one error for another. A missed status update is silent, because `not_application` emails are not reviewed, so everyday use in Version 0.5 is where a repeat would show. The rules came from this same dataset, so the rerun shows these failures are fixed, not that the rules hold on new mail.

## Version 0 completion gate

Version 0 is complete when all five issues meet their acceptance criteria. Version 0.5 begins only after the evaluation records `proceed`.

# Milestone Version 0.5 Make It Repeatable

## Version 0.5 outcome

Move the proven analyser and matching into a small local tool alongside the expense code. The agent retrieves Gmail messages through MCP, and the tool owns message-ID deduplication, analyser calls, matching, review state, and the application list in its own private database.

## Issue J0.5-1 Build one repeatable local job-tracker run

**Type:** AFK

**Status:** `complete` (2026-10-18. Triage chose a terminal review like the expense app's, the commands `jobs-process`, `jobs-review`, and `jobs-list`, and a skim list of job-related `not_application` emails in each run result; see the MVP's [review](MVP.md#review) and [run result](MVP.md#run-result). Searching and selecting against the job tracker's own store is left to J0.5-2.)

**Blocked by:** J0-5 with a `proceed` decision

### What to build

Create one command that accepts a batch of full Gmail messages from the agent and applies the whole workflow. It reuses the existing Gmail normalization unchanged, skips known message IDs, analyses every new message, matches status updates to applications, and exposes review items.

### Acceptance criteria

- [x] One command accepts full message envelopes and processes them end to end.
- [x] Known Gmail message IDs are skipped before any model call.
- [x] Stored decisions are limited to `status_update`, `not_application`, and `needs_review`.
- [x] At most one application exists per normalized company and job title.
- [x] Every application links to all of its source Gmail message IDs.
- [x] The user can accept, correct, or reject a review item without changing the source email.
- [x] The application list shows company, job title, current status, and latest update date.
- [x] The job-tracker database is separate from the expense database, and expense behavior is unchanged.
- [x] Tests use sanitized fixtures and mocked model responses, and cover all three decisions, missing company or title, multiple updates to one application, two roles at one company, out-of-order emails, re-application after rejection, contradictory outcomes, likely matches and aliases, invalid model output, replay, and review correction.
- [x] Credentials, real email content, and databases are excluded from version control.

## Issue J0.5-2 Retrieve job messages through the Gmail API

**Type:** AFK

**Status:** `complete` (2026-10-22. Triage chose a `jobs-sync` command on the Gmail API only, with the expense app's read-only sign-in and no connector skill; a default of 25 new messages per run with `--limit` for larger backlog batches; manual runs only; and a search from 2026-04-14 on every run, with no metadata requests for processed IDs, instead of a full search after failures. Handoff B's backlog count, about 5,200 messages at about 0.5 s per sequential metadata request, then led to requesting metadata only for the oldest window that holds a run's batch. See the MVP's [Gmail retrieval](MVP.md#gmail-retrieval) and the contract's [inbox retrieval](job-tracker-contract.md#1-inbox-retrieval). The first real sync is left to J0.5-3.)

**Blocked by:** J0.5-1

### What to build

Add `jobs-sync`, which searches, selects, and fetches new inbox messages through the shared Gmail API retrieval against the job tracker's own store, and processes them through the existing job processor. Make the expense skip filter and its search terms parameters of the shared selection, so expense behavior is unchanged, and retry rate-limited Gmail requests so backlog runs do not fail on quota.

### Acceptance criteria

- [x] `jobs-sync` reads the shared sign-in without opening a browser, takes the job tracker's own run lock, and checks the analyser runtime before any Gmail request.
- [x] It searches every inbox message since 2026-04-14 with no skip filter, selects against the job store only, fetches `raw`, and normalizes preferring HTML.
- [x] Processed IDs get no metadata request, a backlog run requests metadata only for the oldest window that holds its batch, and a message that failed before newer ones is found by the next run.
- [x] Rate-limited requests are retried with backoff, and tests show a run succeeding through refusals and failing cleanly past the retries.
- [x] The report matches `jobs-process --selection`, including `skim`, with an `already_processed` count; any failure exits with status 1, and a held lock reports `locked`.
- [x] The expense sync's search, requests, and report are unchanged, and its tests pass unchanged.
- [x] Tests use fictional fixtures and a fake Gmail client only; a dry run against a scratch database succeeds.

## Issue J0.5-3 First sync and approval

**Type:** HITL

**Status:** `needs-triage`

**Blocked by:** J0.5-2

### What to build

Run the first sync over emails received on or after 2026-04-14, in as many `jobs-sync` runs as the backlog needs, then verify an incremental run. Finish with a `proceed`, `revise`, or `stop` decision.

### Acceptance criteria

- [ ] The first sync retrieves all inbox emails received on or after 2026-04-14, and none earlier.
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
