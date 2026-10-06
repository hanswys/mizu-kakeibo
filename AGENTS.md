# Agent Instructions

## Documentation ownership

Before changing product scope, technical behavior, or delivery plans, read the relevant authoritative document:

- `MVP.md` is the source of truth for product purpose, user-visible workflow, requirements, exclusions, and the definition of done.
- `docs/inbox-expense-contract.md` is the source of truth for schemas, field definitions, technical behavior, and invariants.
- `ROADMAP.md` is the source of truth for implementation issues, dependencies, milestone gates, and delivery status.

Keep each fact in the document that owns it. Link to authoritative requirements instead of copying their full wording into another document.

## Updating documentation

When making a product change:

1. Update `MVP.md` first.
2. Update `docs/inbox-expense-contract.md` if the change affects technical behavior, data, validation, or persistence.
3. Update `ROADMAP.md` only if the change creates, removes, reprioritizes, or changes delivery work.

An edit does not need to touch all three documents. Before finishing, check whether the change crosses any of these ownership boundaries and update only the affected documents.

When changing implementation status, issue dependencies, or milestone gates without changing product requirements, update only `ROADMAP.md`.
