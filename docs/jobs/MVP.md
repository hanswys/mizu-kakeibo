# Job-Application Tracker MVP

## Document authority

This document is the source of truth for the job tracker's product scope: the problem being solved, the user-visible workflow, the required capabilities, what is out of scope, and the definition of done.

It does not track implementation status or prescribe detailed schemas and invariants. Use the [delivery roadmap](ROADMAP.md) for sequencing, dependencies, and completion gates. The technical contract, [`job-tracker-contract.md`](job-tracker-contract.md), owns schemas, matching and normalization rules, and the sampled email layouts.

## Product idea

The job tracker follows the status of the user's job applications using the emails they cause in Gmail: notifications from LinkedIn and Jobstreet, and messages from employers, their hiring systems, and recruiters. Those emails report events such as an application being submitted, viewed, rejected, or advanced to an interview.

Sampling showed that LinkedIn and Jobstreet send only submitted, viewed, and rejected notices. Interview invitations and most outcomes come from employers' own addresses and hiring systems, so a filter on known senders would miss them. The job tracker therefore has no sender filter. Gmail's own search keeps only inbox messages that contain at least one of a short list of job words, such as application, interview, or role, and the analyser decides which of those concern a job application.

The job-word search was adopted on 2026-10-23, before the first sync, because analysing every message took about 7 s each and the first run alone would have taken close to an hour. It cut the messages since the start from 453 to 191. Every one of the 46 real status emails and 7 review emails in the private evaluation set contains a job word. The trade-off is accepted: an email without any job word is never analysed, and nothing reports it, so a status email worded without them is missed silently. The contract lists the words.

## Core workflow

Applications are stateful entities. One application is updated by several emails over time, whereas each expense is an independent event. Matching a new email to the right application is the core problem.

```text
New Gmail inbox message that contains a job word
  -> Skip if the Gmail message ID was already processed by the job tracker
  -> Retrieve the full message
  -> LLM analysis returns a structured result
  -> Validate the result
      -> Status update: match on company + job title
          -> exact match: update that application
          -> likely match: add the message to the review queue with the suggested application
          -> no match: create a new application
      -> Not an application: record the decision without changing applications
      -> Uncertain or incomplete: add the message to the review queue
```

The LLM only reads the email and fills in a fixed structured result. Matching, status changes, and database writes are done by application code, so they are deterministic and testable.

## Message decisions

Every successfully processed Gmail message ends with one decision:

- `status_update` means the email reports a status for an identified application.
- `not_application` means the email reports no application status, such as a job alert, job recommendation, saved-job reminder, recruiter outreach, newsletter, or any message unrelated to jobs.
- `needs_review` means the analyser could not decide safely or could not extract the required values.

An email must return `needs_review` when the company or the job title is missing, or when the reported status does not clearly map to one of the application statuses below.

`needs_review` flags the email, not the application. While an email waits for review, the application it may concern keeps its last confirmed status.

Emails that mention an application but report no status change are `not_application`. This includes notices that a job listing has closed while the employer keeps the application, and digests that summarize several applications in one email.

## Application statuses

An application has exactly one current status:

- `applied` means the application was submitted.
- `viewed` means the employer viewed the application.
- `interview` means the employer invited the user to interview or advanced the application.
- `rejected` means the employer declined the application.
- `offered` means the employer made an offer.

### Status changes

Status updates are applied to an application in email received order, and statuses only move forward:

```text
applied -> viewed -> interview -> offered | rejected
```

`offered` and `rejected` are final outcomes. Either may follow any earlier status, so a status may be skipped.

- An update to a later status replaces the current status.
- An update to the same status, or to an earlier status, is linked to the application without changing its status. This covers duplicate emails and emails that arrive out of order.
- An `applied` update for an application whose status is `rejected` reopens it as `applied`, because the user applied to the same role again. The earlier emails stay linked.
- An `offered` update for a `rejected` application, or a `rejected` update for an `offered` application, is contradictory. The email goes to review and the application keeps its status. If the user confirms the email in review, the application takes the confirmed status, as the user chose on 2026-10-17.

## Application identity

An application is identified by its company and job title together. Two roles at the same company are two applications. At most one application exists for each company and job title pair.

The analyser extracts a clean company name and job title even when an email combines them with other text, such as a location. Matching is then done by application code:

- Company names and job titles are compared after deterministic normalization, such as case, whitespace, punctuation, and legal-form variants like `Sdn Bhd` and `Sdn. Bhd.`. The contract defines the exact rules.
- A status update whose normalized company and job title match an existing application, directly or through a confirmed alias, updates that application.
- A status update with no exact match but a likely match goes to review with the likely application suggested. A likely match has the same company with a job title that contains or is contained in the existing one, or the same job title with a company name that contains or is contained in the existing one, such as a group name around the brand, as the user chose on 2026-10-17.
- A status update with no exact or likely match creates an application, whatever status it reports. An application first seen as `rejected` is still recorded.
- An email missing either the company or the job title is never matched or used to create an application. It goes to review.

When the user confirms during review that a differently written company name or job title belongs to an existing application, the tracker keeps that name as an alias, so later emails with the same name match automatically.

Applications made through any channel are tracked, including LinkedIn, Jobstreet, other job boards, company career sites, and recruiters, as long as they produce an email.

## Minimum record

Each application retains its company, job title, current status, any confirmed aliases, and a traceable link to every Gmail message that updated it. Each processed message retains its source provider and Gmail message ID, the decision and sanitized reason, any missing fields, any suggested application, and the analyser versions needed for reproducibility.

Field definitions belong in the contract.

## First run

The first run processes inbox messages with a job word received on or after 2026-10-08, Malaysia time, as the user chose on 2026-10-22. This is a fixed date, not a rolling window. Older emails are ignored, so a later update for an application made before 2026-10-08 has no earlier record to match and creates a new application. Later runs process only messages not yet recorded as processed.

The backlog takes several runs. Each run processes the oldest new messages up to its batch size, 25 unless the user asks for more, and reports the rest as deferred to a later run, as the user chose on 2026-10-22.

## MVP components

### Gmail retrieval

- Search the inbox messages that contain a job word, without a sender filter or a Gmail category exclusion.
- Keep the Gmail message ID as the unique source identifier.
- Skip processed IDs unless the user explicitly requests re-evaluation.
- Retrieve and normalize the full message using the same Gmail adapter and `inbox-message/v0` envelope as the [expense contract](../expenses/inbox-expense-contract.md).
- Prefer the HTML alternative over the plain-text alternative when normalizing. Some LinkedIn notifications carry the job title and status only in their HTML part. The shared adapter offers this as an option that the expense app does not use, so expense behavior is unchanged.
- Keep its own processed-message tracking and its own private database, separate from the expense app. The two apps do not share processing state.
- Read Gmail through the Gmail API with the expense app's read-only Google sign-in rather than its own, as the user chose on 2026-10-16, in one sync command the user runs by hand, as the user chose on 2026-10-22. Version 1 also runs it every hour; see [Scheduled sync](#scheduled-sync). No agent or Gmail connector takes part. The sign-in is shared; searches and processing state are not.
- Search the whole period since the first-run start on every run, so a message that failed in an earlier run is always found again.

### Status analyser

- Receive one normalized full message at a time.
- Return only `status_update`, `not_application`, or `needs_review`.
- Extract company, job title, and status only for a `status_update`.
- Ignore other jobs listed in the same email, such as recommendations, when extracting company and job title.
- Return `needs_review` when the company or job title is missing, the status is unclear, or the content cannot be interpreted safely.
- Produce structured output that can be validated before anything is persisted.
- From Version 1.1, also read the interview time from an email that reports the `interview` status; see [interview reminders](#interview-reminders).

### Application matching

- Match each validated status update to an application by normalized company and job title, including confirmed aliases.
- Send likely but inexact matches to review with the suggested application.
- Create the application when no match exists.
- Apply the status change rules above.
- Link every processed status-update email to its application.

### Review

Review runs in the terminal, like the [expense review](../expenses/MVP.md#review), as the user chose on 2026-10-18. A numbered list shows each waiting email by date, sender, subject, and why it waits. Opening an item shows the proposal with the first lines of the email, and keys show the whole text or open the original message in Gmail.

- Show the source email beside the proposed decision, extracted values, and any suggested application.
- Allow the user to accept the proposal, correct company, job title, or status, attach the email to the suggested application, another application, or a new one, or mark the email as not an application.
- Save a confirmed name variant as an alias of the application.
- Apply a resolved status update through the same matching and status change rules.
- Preserve the Gmail message ID and the original decision after correction.

### Application list

- Show every application in the terminal with its company, job title, current status, and the date of its latest update.
- From Version 1.1, order the list by how far each application got: `offered`, `interview`, `viewed`, then `applied`, with `rejected` last, and within each status the most recently updated first. The user chose this on 2026-10-25, so the applications that need attention come first and closed ones sink to the bottom.
- Once the first sync has created the database, the expense app's [spending summary](../expenses/MVP.md#spending-summaries) also counts the applications and the emails waiting for review, and lists the job commands, as the user chose on 2026-10-22. Reading the counts changes nothing and does not wait for a sync. Day-to-day use is described in the [user guide](USER-GUIDE.md).

### Run result

- Report which emails became status updates, were not applications, went to review, were skipped, failed, or were deferred, and how many applications were created, advanced, reopened, or left unchanged.
- List the `not_application` emails that look job-related, such as those from LinkedIn or Jobstreet status senders or hiring systems, or with an application or interview in the subject, by sender and subject. A missed status update is otherwise silent, because `not_application` emails are not reviewed, so the user can skim this list for one, as the user chose on 2026-10-18. The list changes nothing and is not stored.

### Testing

Parsing and matching are tested before the tool is built. A private evaluation set of real inbox emails, with human ground truth, must cover LinkedIn, Jobstreet, and employer or hiring-system senders; every status; non-application emails, including job alerts and unrelated mail; emails missing a company or job title; several emails that update the same application; names written differently across emails for the same application; and a re-application after a rejection. No real offer email exists in the sampling window, so `offered` is covered by a few synthetic emails, clearly marked as synthetic. The [roadmap](ROADMAP.md) owns the evaluation gates.

## Scheduled sync

Version 1 syncs the job tracker every hour by itself, after the MVP is complete. The user chose this design on 2026-10-23, because a sync run by hand happens only when remembered, so status updates and review items wait.

- Run as a second step of the expense app's [scheduled sync](../expenses/MVP.md#scheduled-sync), after its expense step, rather than as a separate schedule. Both apps then use the same hours, the same checks that the laptop is open, awake, and charged enough, and the same Gmail sign-in and analyser checks, and the analyser handles one app at a time. This is the second surface the two projects share, after the [spending summary](#application-list).
- Keep processing separate. The job step has its own run lock, processed messages, database, and record of each run. A job sync started by hand that is still running skips the job step that hour, and does not stop the expense step. A failure in either step does not stop the other. Only a failed Gmail sign-in or analyser check stops both, because they are shared.
- Select messages as a manual sync does: the oldest new messages, at most 25 per hour, with the rest deferred to the next hour. Every sync searches from the first-run start, so a message that failed is retried the next hour without special handling.
- After a job step that created, advanced, or reopened an application, sent an email to review, or failed a message, show a notification of its own, separate from the expense notification, with the counts of updates, review items, and failures. A step with only duplicates or non-applications stays silent. No dialog opens. Job review can wait, so it is not interrupted for.
- When the job step stops before reading any message because of its own failure, such as its Gmail search, show a notification saying so, at most once a day. A failed sign-in or analyser check is reported once, by the expense app's failure dialog.
- Only a scheduled sync shows a job notification. A job sync started by hand shows its run result as before.
- Keep a private record of each job step's message IDs, counts, and errors for 90 days, never message content, apart from the expense record. The expense app's last-sync line keeps showing the expense step.

### Skim list

Nobody reads a scheduled run's result, so the [skim list](#run-result) needs another place:

- A `jobs-skim` command lists the job-related `not_application` emails received in a period, by date, sender, and subject. The default is this week, and it accepts the same periods as the expense app's spending command. It is worked out from the stored decisions, so nothing new is stored, and an email can be opened in Gmail from the list. A missed status update is still corrected by re-evaluation.
- The `Jobs` row of the [spending summary](../expenses/MVP.md#spending-summaries) adds the number of emails to skim this week when there are any.

## Application history and interviews

The user asked on 2026-10-25 for quick context before a call or interview, and to be reminded of each interview, because interview emails are the ones that matter most and are easy to lose among the rest.

### Application history

- `jobs-list` numbers its applications, and `jobs-show` with a number shows that application's company, job title, current status, and confirmed aliases.
- Below them it lists every email linked to the application, oldest first, with its received date, sender, subject, the status it reported, and what it did: created, advanced, or reopened the application, or left it unchanged.
- In a terminal, a listed email opens in Gmail by its number.
- Showing an application changes nothing.

### Interview reminders

- When an email reports the `interview` status for an application, the interview's date and time are read from it when the email states them. An invitation that only asks the user to pick a slot, such as a booking link, has no time yet. A later email for the same application that confirms or moves the time replaces it.
- A time must be supported by the email's text, like a company or job title. A time that is unclear, or whose time zone is ambiguous, is left unknown rather than guessed. Malaysia time is assumed only when the email names no time zone and nothing suggests another.
- The interview list shows upcoming interviews by date and time with the company and job title, and interviews whose time is unknown. The user can set or correct an interview's time by hand. A time set by hand is never replaced by a later email.
- The scheduled sync shows a reminder notification on the day before an interview and on the morning of it, each at the first scheduled hour of that day. A reminder missed while the laptop was closed is shown at the next scheduled hour that is still before the interview. Each reminder is shown once.
- The [spending summary](../expenses/MVP.md#spending-summaries) shows the next interview in the `Jobs` row.
- An interview with a known time can be added to the user's calendar, so it appears on the phone as well. Settled in the roadmap's triage: the macOS Calendar app on the laptop, or Google Calendar, which needs a permission beyond today's read-only Gmail sign-in and so changes the sign-in both apps share.
- Interview times are evaluated on private interview emails before reminders depend on them, as the analyser was.

## Privacy

The same rules as the expense project apply: no real email content, identifiers, or personal data may be committed to the repository. Samples of email structure must record field names, types, and layout only.

## Out of scope

The MVP does not require:

- Applications that never produce an email, or manual entry of applications.
- Emails received before 2026-10-08.
- Attachment retrieval or analysis, including offer letters sent as attachments.
- Fuzzy matching that updates an application without review.
- Calendar events, reminders, or follow-up prompts. Version 1.1 adds [interview reminders](#interview-reminders) and their calendar events; other reminders and follow-up prompts stay out of scope.
- Reporting, analytics, or visual dashboards beyond the application list and an [application's history](#application-history).
- Replying to or acting on emails.
- Sharing ingest or processing state with the expense app.
- Gmail labels or filters, Gmail push notifications, or scheduled automation. Version 1 adds the [scheduled sync](#scheduled-sync) after the MVP.
- Hosted deployment, multi-user accounts, or native mobile applications.

## Definition of done

The MVP is complete when, with each command started by hand in the terminal:

1. A sync finds inbox messages not yet processed by the job tracker, limited to messages with a job word received on or after 2026-10-08.
2. A sync does not reprocess Gmail message IDs already recorded.
3. A sync sends every new message through the same status analyser.
4. A sync records each successful analysis as `status_update`, `not_application`, or `needs_review`.
5. A sync creates or updates exactly one application per company and job title, applies the status change rules, and links every source message.
6. Review presents review items, including suggested matches, beside their source emails and applies resolved items to the application list.
7. The application list shows each application's current status.

The analyser and matching must first pass the private evaluation described under [Testing](#testing).
