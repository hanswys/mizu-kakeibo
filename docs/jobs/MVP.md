# Job-Application Tracker MVP

**Status:** Draft. Only the known goals and constraints are recorded; everything else is an open question. There is no contract or roadmap for this project yet.

## Document authority

This document will be the source of truth for the job tracker's product scope: the problem being solved, the user-visible workflow, the required capabilities, what is out of scope, and the definition of done.

It does not track implementation status or prescribe detailed schemas and invariants. A technical contract and delivery roadmap will be added under `docs/jobs/` once the open questions below are resolved.

## Product idea

The job tracker follows the status of the user's job applications using the notification emails that LinkedIn and Jobstreet send to Gmail. Those emails report events such as an application being submitted, viewed, rejected, or advanced to an interview.

Unlike the [expense MVP](../expenses/MVP.md), which considers every inbox message because expense senders are not known in advance, the job tracker only considers messages from known LinkedIn and Jobstreet senders. A sender filter is appropriate here.

## Core workflow

Applications are stateful entities. One application is updated by several emails over time, whereas each expense is an independent event. Matching a new email to an existing application is the core problem.

```text
New Gmail message from a known LinkedIn or Jobstreet sender
  -> Skip if the message was already processed for the job tracker
  -> Retrieve the message
  -> Extract the application and its reported status
  -> Match it to an existing application, or create a new one
      -> Clear match: apply the status update
      -> Ambiguous match or status: hold for review (open question)
```

The exact steps, including review handling, are not settled.

## Application statuses

Open. The status vocabulary and the valid transitions between statuses have not been defined. Example statuses seen in the product idea are `applied`, `viewed`, `rejected`, and `interview`; these are illustrative, not a decided list.

## Minimum record

Open. At minimum, each application will need a stable identity, its current status, and a traceable link to every source Gmail message that updated it. Field definitions belong in the future contract.

## MVP components

### Gmail retrieval

- Search for messages from known LinkedIn and Jobstreet senders.
- Keep the Gmail message ID as the source identifier.
- Whether this shares the expense app's ingest is an open question.

### Status extraction

- Extract enough to identify the application and its reported status from each message.
- The parsing approach is an open question.

### Application matching

- Match each status update to an existing application or create a new one.
- The matching rule and ambiguity handling are open questions.

### Review

- Open. Likely needed for ambiguous matches or unrecognized emails.

## Privacy

The same rules as the expense project apply: no real email content, identifiers, or personal data may be committed to the repository. Samples of LinkedIn and Jobstreet email structure must record field names, types, and layout only.

## Open questions

- What is the status vocabulary, and which transitions are valid?
- How is an email matched to an application (company and title, a job ID in links, or something else), and what happens on ambiguity (for example, a review queue)?
- How are out-of-order, duplicate, or contradictory status emails handled?
- Are emails parsed with deterministic templates, the LLM, or templates with an LLM fallback?
- Does the job tracker share the expense app's Gmail ingest and processed-message store? Today the expense store's `messages` and `analyses` record one expense decision per Gmail ID, so a shared store needs per-app processing state.
- What do real LinkedIn and Jobstreet emails look like? Their structure must be sampled through the Gmail MCP (read-only, structure only) before the contract is written. The Gmail connector currently needs re-authorization on claude.ai.

## Out of scope

Open. Job boards other than LinkedIn and Jobstreet are not part of the stated goal; the remaining exclusions have not been decided.

## Definition of done

Open. To be defined once the status vocabulary, matching rule, and parsing approach are decided.
