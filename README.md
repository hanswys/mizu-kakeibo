# Mizu Kakeibo

Mizu Kakeibo is a local, contract-driven inbox-to-expense processor. The product
scope lives in [`docs/expenses/MVP.md`](docs/expenses/MVP.md), and the
implemented technical behavior lives in
[`docs/expenses/inbox-expense-contract.md`](docs/expenses/inbox-expense-contract.md).

## Setup

```sh
uv sync --extra dev
```

The analyser requires Ollama `0.23.1` with the exact `qwen2.5:14b` build named in
the contract. Data files passed to the commands below are private and must remain
outside version control.

## Commands

Normalize one Gmail MCP `read_email(format="full")` response, or a JSON array of
responses:

```sh
uv run mizu-kakeibo normalize private-gmail-batch.json --output private-envelopes.json
```

Before retrieving full messages, filter a `search_email_ids` response against the
processed-message store:

```sh
uv run mizu-kakeibo filter-new private-search-result.json \
  --database private-kakeibo.db --ledger private-ledger.csv
```

Process either full Gmail responses or normalized envelopes through Ollama and
persist the decisions:

```sh
uv run mizu-kakeibo process private-envelopes.json \
  --database private-kakeibo.db --ledger private-ledger.csv
```

Use `--reevaluate` to explicitly replace the active analysis for an already
processed source. Review items can be listed and resolved without modifying the
source message:

```sh
uv run mizu-kakeibo review-list --database private-kakeibo.db
uv run mizu-kakeibo review-resolve gmail MESSAGE_ID not_expense \
  --database private-kakeibo.db --ledger private-ledger.csv
```

For an accepted or corrected expense, add `--merchant`, `--amount-minor`,
`--currency`, and `--transaction-date` to `review-resolve`.

## Verification

```sh
uv run pytest
uv run ruff check .
```
