# Agent Instructions

## Projects

This repository holds several inbox-driven personal apps that are planned to converge into one app. Each project keeps its own documentation under `docs/<project>/`:

| Project | Directory | Status |
| --- | --- | --- |
| Mizu Kakeibo (Gmail to expense ledger) | `docs/expenses/` | Active |
| Job-application tracker (job-application emails, including LinkedIn and Jobstreet) | `docs/jobs/` | Planning |

## Documentation ownership

Each project has three authoritative documents. Before changing a project's product scope, technical behavior, or delivery plans, read the relevant one:

- `docs/<project>/MVP.md` is the source of truth for product purpose, user-visible workflow, requirements, exclusions, and the definition of done.
- The project's contract is the source of truth for schemas, field definitions, technical behavior, and invariants. For `expenses`, this is `docs/expenses/inbox-expense-contract.md`.
- `docs/<project>/ROADMAP.md` is the source of truth for implementation issues, dependencies, milestone gates, and delivery status.

A draft project may not have a contract or roadmap yet. Do not create them until the project's MVP is settled enough to need them.

Keep each fact in the document that owns it. Link to authoritative requirements instead of copying their full wording into another document, including across projects.

## Updating documentation

When making a product change within a project:

1. Update that project's `MVP.md` first.
2. Update that project's contract if the change affects technical behavior, data, validation, or persistence.
3. Update that project's `ROADMAP.md` only if the change creates, removes, reprioritizes, or changes delivery work.

An edit does not need to touch all three documents. Before finishing, check whether the change crosses any of these ownership boundaries and update only the affected documents.

When changing implementation status, issue dependencies, or milestone gates without changing product requirements, update only the project's `ROADMAP.md`.

## Shared infrastructure

Gmail ingest, the `inbox-message/v0` envelope, and processed-message tracking are currently defined in the expense contract but may be shared by other projects. There is no shared-layer document yet. When a change affects any of these, flag it explicitly to the user and note the impact on every project that depends on it.

The job tracker reuses the Gmail adapter and the `inbox-message/v0` envelope, turning on an adapter option that prefers the HTML alternative, which the expense app leaves off. It keeps its own inbox search, processed-message tracking, and database. A change to the adapter or envelope therefore affects both projects; a change to either project's processing state affects only that project.
