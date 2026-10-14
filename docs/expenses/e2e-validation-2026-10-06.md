# End-to-End Validation — 2026-10-06

## Result

The local inbox-to-expense workflow produced the expected result for all 21
human-checked messages.

| Test set | Messages | Human result | Model result | Outcome |
| --- | ---: | --- | --- | --- |
| Latest inbox batch | 20 | 20 `not_expense` | 20 `not_expense` | 20/20 matched |
| Targeted real expense | 1 | One completed expense | One completed expense | Matched |

The accepted expense was extracted as:

- Merchant: Grab Malaysia
- Amount: MYR 21.00 (`amount_minor` 2100)
- Transaction date: 2026-10-05
- Missing fields: none

## Checks

- False expenses: 0
- Missed expenses: 0
- Correct non-expenses: 20
- Correct expenses: 1
- Review decisions: 0
- Exact complete-transaction matches: 1/1
- Immediate batch replay: all 20 messages skipped without model calls
- Duplicate ledger rows: 0

One message returned an invalid structured model response on two attempts. The
workflow correctly left it retryable and persisted nothing from either invalid
response. A later retry returned valid output and was stored normally.

## Scope

This was an ad hoc real-inbox validation with human review after the model run.
It is useful integration evidence, but it is not the fixed, pre-labelled private
evaluation required by V0-2 in the [delivery roadmap](ROADMAP.md). No email
content, Gmail identifiers, or other private source data is recorded here.
