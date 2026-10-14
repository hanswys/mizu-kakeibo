# Job-Application Tracker MVP

## Document authority

This document is the source of truth for the job tracker's product scope: the problem being solved, the user-visible workflow, the required capabilities, what is out of scope, and the definition of done.

It does not track implementation status or prescribe detailed schemas and invariants. Use the [delivery roadmap](ROADMAP.md) for sequencing, dependencies, and completion gates. A technical contract will be added under `docs/jobs/` once real email layouts have been sampled; the roadmap tracks that work.

## Product idea

The job tracker follows the status of the user's job applications using the notification emails that LinkedIn and Jobstreet send to Gmail. Those emails report events such as an application being submitted, viewed, rejected, or advanced to an interview.

Unlike the [expense MVP](../expenses/MVP.md), which considers every inbox message because expense senders are not known in advance, the job tracker only considers messages from known LinkedIn and Jobstreet senders. A sender filter is appropriate here.

## Core workflow

Applications are stateful entities. One application is updated by several emails over time, whereas each expense is an independent event. Matching a new email to the right application is the core problem.

```text
New Gmail message from a known LinkedIn or Jobstreet sender
  -> Skip if the Gmail message ID was already processed by the job tracker
  -> Retrieve the full message
  -> LLM analysis returns a structured result
  -> Validate the result
      -> Status update: match on company + job title, then update
         that application or create a new one
      -> Not an application: record the decision without changing applications
      -> Uncertain or incomplete: add the message to the review queue
```

The LLM only reads the email and fills in a fixed structured result. Matching, status changes, and database writes are done by application code, so they are deterministic and testable.

## Message decisions

Every successfully processed Gmail message ends with one decision:

- `status_update` means the email reports a status for an identified application.
- `not_application` means the email is from a known sender but reports no application status, such as a job alert, job recommendation, or newsletter.
- `needs_review` means the analyser could not decide safely or could not extract the required values.

An email must return `needs_review` when the company or the job title is missing, or when the reported status does not clearly map to one of the application statuses below.

`needs_review` flags the email, not the application. While an email waits for review, the application it may concern keeps its last confirmed status.

## Application statuses

An application has exactly one current status:

- `applied` means the application was submitted.
- `viewed` means the employer viewed the application.
- `interview` means the employer invited the user to interview or advanced the application.
- `rejected` means the employer declined the application.
- `offered` means the employer made an offer.

Rules for which status changes are allowed, and how out-of-order, duplicate, or contradictory emails are applied, are still open (see [open questions](#open-questions)).

## Application identity

An application is identified by its company and job title together. Two roles at the same company are two applications. At most one application exists for each company and job title pair.

- A status update whose company and job title match an existing application updates that application.
- A status update with no matching application creates one, whatever status it reports. An application first seen as `rejected` is still recorded.
- An email missing either the company or the job title is never matched or used to create an application. It goes to review.

How company names and job titles are normalized before comparison is defined in the future contract, based on the sampled emails.

## Minimum record

Each application retains its company, job title, current status, and a traceable link to every Gmail message that updated it. Each processed message retains its source provider and Gmail message ID, the decision and sanitized reason, any missing fields, and the analyser versions needed for reproducibility.

Field definitions belong in the future contract.

## First run

The first run processes LinkedIn and Jobstreet emails received on or after 2026-04-14, six months before this scope was set. Older emails are ignored. Later runs process only messages not yet recorded as processed.

## MVP components

### Gmail retrieval

- Search for messages from known LinkedIn and Jobstreet senders only. The exact sender addresses are recorded in the contract after sampling.
- Keep the Gmail message ID as the unique source identifier.
- Skip processed IDs unless the user explicitly requests re-evaluation.
- Retrieve and normalize the full message using the same Gmail adapter and `inbox-message/v0` envelope as the [expense contract](../expenses/inbox-expense-contract.md), unchanged.
- Keep its own processed-message tracking and its own private database, separate from the expense app. The two apps do not share processing state.

### Status analyser

- Receive one normalized full message at a time.
- Return only `status_update`, `not_application`, or `needs_review`.
- Extract company, job title, and status only for a `status_update`.
- Return `needs_review` when the company or job title is missing, the status is unclear, or the content cannot be interpreted safely.
- Produce structured output that can be validated before anything is persisted.

### Application matching

- Match each validated status update to an application by company and job title.
- Create the application when no match exists.
- Link every processed status-update email to its application.

### Review

- Show the source email beside the proposed decision and extracted values.
- Allow the user to accept the proposal, correct company, job title, or status, or mark the email as not an application.
- Apply a resolved status update through the same matching rules.
- Preserve the Gmail message ID and the original decision after correction.

### Application list

- Show every application with its company, job title, current status, and the date of its latest update.

### Testing

Parsing and matching are tested before the tool is built. A private evaluation set of real LinkedIn and Jobstreet emails, with human ground truth, must cover both senders, every status, non-application emails, emails missing a company or job title, and several emails that update the same application. The [roadmap](ROADMAP.md) owns the evaluation gates.

## Privacy

The same rules as the expense project apply: no real email content, identifiers, or personal data may be committed to the repository. Samples of LinkedIn and Jobstreet email structure must record field names, types, and layout only.

## Open questions

These must be decided before the contract is written:

- Which status changes are allowed, and how are out-of-order, duplicate, or contradictory emails applied? The current direction is to order updates by email received time and rely on the one-application-per-company-and-title rule, but this has not been settled.
- How are near-duplicate company names or job titles handled, for example a legal company name in one email and a brand name in another? The sampled emails will show how consistent the names are.

## Out of scope

The MVP does not require:

- Job boards or senders other than LinkedIn and Jobstreet.
- Applications that never produce a LinkedIn or Jobstreet email, or manual entry of applications.
- Emails received before 2026-04-14.
- Attachment retrieval or analysis.
- Calendar events, reminders, or follow-up prompts.
- Reporting, analytics, or visual dashboards beyond the application list.
- Replying to or acting on emails.
- Sharing ingest or processing state with the expense app.
- Gmail labels or filters, Gmail push notifications, or scheduled automation.
- Hosted deployment, multi-user accounts, or native mobile applications.

## Definition of done

The MVP is complete when one agent request:

1. Finds LinkedIn and Jobstreet messages not yet processed by the job tracker, limited on the first run to messages received on or after 2026-04-14.
2. Does not reprocess Gmail message IDs already recorded.
3. Sends every new message through the same status analyser.
4. Records each successful analysis as `status_update`, `not_application`, or `needs_review`.
5. Creates or updates exactly one application per company and job title, linked to every source message.
6. Presents review items beside their source emails and applies resolved items to the application list.
7. Shows the application list with each application's current status.

The analyser and matching must first pass the private evaluation described under [Testing](#testing).
