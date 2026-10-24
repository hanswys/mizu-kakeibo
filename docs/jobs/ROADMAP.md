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
| J1-1 | Run jobs-sync as a step of the hourly scheduled sync | AFK | J0.5-3 proceed gate |
| J1-2 | List the emails to skim with jobs-skim | AFK | J0.5-3 proceed gate |
| J1-3 | Verify the scheduled job step in everyday use | HITL | J1-1, J1-2 |

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

Move the proven analyser and matching into a small local tool alongside the expense code. The tool retrieves Gmail messages through the Gmail API with `jobs-sync`, and owns message-ID deduplication, analyser calls, matching, review state, and the application list in its own private database.

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

**Status:** `complete` (2026-10-22. Triage chose a `jobs-sync` command on the Gmail API only, with the expense app's read-only sign-in and no connector skill; a default of 25 new messages per run with `--limit` for larger backlog batches; manual runs only; and a search from the start on every run, with no metadata requests for processed IDs, instead of a full search after failures. After handoff B's backlog count, the user moved the start to 2026-10-08 and chose no special quota handling: a rate limit fails as in the expense sync. Handoff B's measure of about 0.5 s per sequential metadata request led to requesting metadata only for the oldest window that holds a run's batch. See the MVP's [Gmail retrieval](MVP.md#gmail-retrieval) and the contract's [inbox retrieval](job-tracker-contract.md#1-inbox-retrieval). The first real sync is left to J0.5-3.)

**Blocked by:** J0.5-1

### What to build

Add `jobs-sync`, which searches, selects, and fetches new inbox messages through the shared Gmail API retrieval against the job tracker's own store, and processes them through the existing job processor. Make the expense skip filter and its search terms parameters of the shared selection, so expense behavior is unchanged.

### Acceptance criteria

- [x] `jobs-sync` reads the shared sign-in without opening a browser, takes the job tracker's own run lock, and checks the analyser runtime before any Gmail request.
- [x] It searches every inbox message since 2026-10-08 with no skip filter, selects against the job store only, fetches `raw`, and normalizes preferring HTML.
- [x] Processed IDs get no metadata request, a backlog run requests metadata only for the oldest window that holds its batch, and a message that failed before newer ones is found by the next run.
- [x] A rate-limited search or metadata request ends the run and records nothing, and a rate-limited fetch fails that message alone.
- [x] The report matches `jobs-process --selection`, including `skim`, with an `already_processed` count; any failure exits with status 1, and a held lock reports `locked`.
- [x] The expense sync's search, requests, and report are unchanged, and its tests pass unchanged.
- [x] Tests use fictional fixtures and a fake Gmail client only; a dry run against a scratch database succeeds.

## Issue J0.5-3 First sync and approval

**Type:** HITL

**Status:** `complete` (2026-10-24, decision `proceed`. Before the first batch finished, the user chose to shorten the backlog: `jobs-sync` now searches only inbox messages with a [job word](job-tracker-contract.md#1-inbox-retrieval), which cut the messages since the start from 453 to 191, about 22 minutes of model time, so the backlog runs as one `--limit 200` sync instead of 25-message batches. Every real status and review email in the J0-4 set contains a job word.)

**Blocked by:** J0.5-2

### What to build

Run the first sync over emails received on or after 2026-10-08, in as many `jobs-sync` runs as the backlog needs, then verify an incremental run. Finish with a `proceed`, `revise`, or `stop` decision.

### Acceptance criteria

- [x] The first sync retrieves all inbox emails with a job word received on or after 2026-10-08, and none earlier.
- [x] The result identifies status-update, not-application, review, skipped, and failed message IDs.
- [x] The user checks the application list against their own knowledge of their applications.
- [x] Review items can be resolved while viewing the original Gmail message.
- [x] An immediate replay makes no model calls for already processed message IDs.
- [x] No credentials, real message contents, or database files enter version control.
- [x] The user records a `proceed`, `revise`, or `stop` decision with reasons.

### J0.5-3 decision

`proceed`, recorded by the user on 2026-10-24 after the first sync, the skim list, and the resolution of every review item. The configuration was the J0-5 `proceed` configuration unchanged: `qwen3.5:4b-q4_K_M`, prompt `jobs-v0.2`, and the missing-value guard.

The backlog ran as one `jobs-sync --limit 200` over the 191 messages with a job word received since the start. It recorded 49 status updates, 126 not applications, and 6 review items, and created 42 applications and advanced 7. 10 messages failed, all on their `raw` fetch with `HTTP 403 rateLimit`, a per-user rate limit that Gmail reports as a 403 rather than a 429. As the contract's [inbox retrieval](job-tracker-contract.md#1-inbox-retrieval) and [processing rules](job-tracker-contract.md#9-processing-failure-and-idempotency) specify, each failure was that message's alone, nothing was recorded for it, and the next run found it again: a second `jobs-sync` processed all 10 cleanly, as 2 status updates, 6 not applications, and 2 review items. A following run processed 1 newly arrived status update, and an immediate replay found 192 messages already processed and made no model calls. The earliest processed message was received 12 seconds after the start.

Against the acceptance criteria:

- **Skim list.** The two batches listed 44 job-related `not_application` emails: job alerts and recommendations, job-listing closures, multi-application digests, an interview survey, an interview recap, and job-board marketing. None was found to be a missed status update, so the silent miss the J0-5 decision warned about did not recur here.
- **Review.** 8 items: 7 analyser items, where the email lacked the company, the job title, or a mappable status, 2 of them through the missing-value guard, and 1 likely match, a longer variant of an existing job title. They were resolved as 6 attaches, 1 create, and 1 not an application, saving 2 confirmed aliases. 4 of the attaches were acknowledgement and assessment emails for one application whose hiring-system and assessment emails name no title.
- **Application list.** 44 applications: 31 applied, 4 viewed, 3 interview, and 6 rejected, with 11 linked to more than one email. The user reported no mismatch. Applications made before the start appear only when a later email reports their status, so job-listing closures and digests that name earlier applications add nothing, as the [MVP](MVP.md#gmail-retrieval) start date intends.

The user chose `proceed` on this evidence: the skim list hid no missed status update, every review item could be resolved, the list showed no mismatch, and the rate-limited fetches recovered on the next run without intervention.

Follow-up: when the missing-value guard replaces an output whose job title is the placeholder `null`, the extracted job title is that placeholder, and `jobs-review`'s attach to another application (`t`) offers it as the default job title. Accepting the default saves a `null` title as a confirmed alias. In this review the application's own title was used instead.

## Version 0.5 completion gate

**Status:** `complete` (2026-10-24. All three issues met their acceptance criteria, and the user recorded `proceed` in [J0.5-3](#j05-3-decision).)

Version 0.5 is complete when all three issues meet their acceptance criteria and the user records `proceed`.

# Milestone Version 1 Sync on a Schedule

## Version 1 outcome

Sync the job tracker every hour without a manual run, as the MVP's [scheduled sync](MVP.md#scheduled-sync) describes. The job tracker becomes a second step of the expense app's `scheduled-sync`, sharing its LaunchAgent, gates, and sign-in and analyser checks but none of its processing state. Version 1 starts only after J0.5-3 records `proceed`, because a schedule repeats whatever `jobs-sync` does every hour.

The scheduled run is shared infrastructure. J1-1 changes the expense app's [scheduled run](../expenses/inbox-expense-contract.md#9-scheduled-run), and J1-2 changes its [`status`](../expenses/inbox-expense-contract.md#10-spending-summaries) Jobs row. Both must leave expense behavior unchanged and update the expense contract in the same change.

## Issue J1-1 Run jobs-sync as a step of the hourly scheduled sync

**Type:** AFK

**Status:** `planned`

**Blocked by:** J0.5-3 with a `proceed` decision

### What to build

Split `scheduled-sync` into shared checks and two steps. The gates, `caffeinate`, sign-in check, and runtime check run once. Then the expense step runs under the expense run lock, and the job step runs `jobs-sync` under the job tracker's run lock with its default batch of 25. Each step is recorded on its own. Add the job notification. Define the job step in the job contract's [processing section](job-tracker-contract.md#9-processing-failure-and-idempotency), and update the expense contract's [scheduled run](../expenses/inbox-expense-contract.md#9-scheduled-run) for the shared checks and the step order.

### Acceptance criteria

- [ ] One LaunchAgent runs both steps every hour from 08:00 to 23:00, the expense step first. The gates, `caffeinate`, sign-in check, and runtime check run once per hour.
- [ ] A held expense lock skips only the expense step, and a held job lock skips only the job step. Each is recorded as `locked`.
- [ ] A failure or unexpected error in either step does not stop the other. A failed sign-in or runtime check stops both, with one expense failure dialog as today.
- [ ] The job step selects at most 25 new messages, oldest first, defers the rest, and finds earlier failures through its search from the start.
- [ ] The job step writes its own run record and log line under `~/mizu-kakeibo-private/jobs/live/`, with message IDs, counts, the skim message IDs, and sanitized errors only, and deletes them after 90 days. The expense run record, `scheduled.log`, and the `status` last-sync line are unchanged.
- [ ] After a job step that created, advanced, or reopened an application, stored a review item, or failed a message, a notification titled for the job tracker shows the counts of updates, review items, and failures. A step with only unchanged links or `not_application` decisions shows nothing, and no dialog opens.
- [ ] A job step that stops before processing because of its own failure shows one notification saying so, at most once a day in Malaysia time.
- [ ] A manual `jobs-sync` and a manual `sync` behave as before and never notify.
- [ ] Expense scheduled-run tests pass unchanged, apart from the step split. New tests cover each lock held, each step failing, the shared checks failing, and the job notification rules, with a fake system and Gmail client.
- [ ] The job [user guide](USER-GUIDE.md) no longer says that nothing runs by itself, and the expense user guide mentions the job step.

## Issue J1-2 List the emails to skim with jobs-skim

**Type:** AFK

**Status:** `planned`

**Blocked by:** J0.5-3 with a `proceed` decision

### What to build

Add `jobs-skim`, which lists the [skim list](MVP.md#skim-list) for a period from the stored decisions, and add a count of this week's emails to skim to the `status` Jobs row. Define both in the job contract beside the [skim list rules](job-tracker-contract.md#skim-list), and update the expense contract's `status` Jobs row. It does not depend on J1-1, so the two can be built at once.

### Acceptance criteria

- [ ] `jobs-skim [period]` lists, oldest first, each `not_application` message received in the period that meets the skim rules, with its received date, sender, and subject. The default is `week`, and it accepts the periods `spend` accepts.
- [ ] In a terminal, a numbered email can be opened in Gmail. Nothing is recorded, and the list is the same before and after.
- [ ] The `status` Jobs row adds `· N to skim this week` when N is above zero. It still reads the database read-only without the run lock, and its output is unchanged when N is zero or there is no job database.
- [ ] `status` lists `jobs-skim` with the other job commands.
- [ ] Tests use fictional fixtures only. The job user guide replaces "glance at `skim` after each sync" with `jobs-skim`.

## Issue J1-3 Verify the scheduled job step in everyday use

**Type:** HITL

**Status:** `planned`

**Blocked by:** J1-1, J1-2

### What to build

Reinstall the command, reload the LaunchAgent if its arguments changed, and use it for a day. Finish with a `proceed`, `revise`, or `stop` decision.

### Acceptance criteria

- [ ] A scheduled hour runs both steps, and each step's record and log line are where J1-1 puts them.
- [ ] A real job status update or review item shows the job notification, and an hour with none stays silent. A mock item may stand in when no real one arrives that day.
- [ ] The expense step's notifications, dialogs, and `status` last-sync line are unchanged.
- [ ] `jobs-skim` and the `status` count show the week's job-related non-applications.
- [ ] No credentials, message contents, or database files enter version control.
- [ ] The user records a `proceed`, `revise`, or `stop` decision with reasons.

## Version 1 completion gate

Version 1 is complete when all three issues meet their acceptance criteria and the user records `proceed`.

## Scope changes

Sharing ingest or processing state with the expense app is excluded by the MVP. Add it here only after the MVP scope changes, as issues with dependencies and completion gates. Scheduled automation was added as [Version 1](#milestone-version-1-sync-on-a-schedule) on 2026-10-23.
