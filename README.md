# Mizu Kakeibo

Mizu Kakeibo is a local, contract-driven inbox-to-expense processor. The product
scope lives in [`docs/expenses/MVP.md`](docs/expenses/MVP.md), and the
implemented technical behavior lives in
[`docs/expenses/inbox-expense-contract.md`](docs/expenses/inbox-expense-contract.md).

## Projects

- Expenses (Mizu Kakeibo): [`docs/expenses/MVP.md`](docs/expenses/MVP.md)
- Job-application tracker (planning): [`docs/jobs/MVP.md`](docs/jobs/MVP.md)

The setup and commands below apply to the expense project.

## Setup

```sh
uv sync --extra dev
```

The analyser requires Ollama `0.23.1` with the exact `qwen2.5:14b` build named in
the contract. Data files passed to the commands below are private and must remain
outside version control.

Homebrew and the Ollama desktop app track newer releases, so install the pinned
command-line build instead (macOS):

```sh
mkdir -p ~/.local/opt/ollama-0.23.1 && cd ~/.local/opt/ollama-0.23.1
curl -fsSL https://github.com/ollama/ollama/releases/download/v0.23.1/ollama-darwin.tgz | tar -xz
ln -sf ~/.local/opt/ollama-0.23.1/ollama ~/.local/bin/ollama
ollama serve &            # or run it from a launchd user agent
ollama pull qwen2.5:14b
```

The commands refuse to run if the Ollama version or model digest differs from the
contract.

## Commands

Normalize one Gmail MCP `read_email(format="full")` response or claude.ai Gmail
connector `get_message(RAW)` response, or a JSON array of responses:

```sh
uv run mizu-kakeibo normalize private-gmail-batch.json --output private-envelopes.json
```

Before retrieving full messages, filter a `search_email_ids` response, or a JSON
array of message IDs, against the processed-message store:

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

Score the analyser against a private labelled dataset without touching the
database or ledger. The dataset format and scoring rules are defined in the
contract's evaluation section. The command prints a sanitized summary; `--output`
writes the full per-example report, which contains message IDs and must stay
private:

```sh
uv run mizu-kakeibo evaluate private-eval-dataset.json --runs 3 \
  --output private-eval-report.json
```

## Verification

```sh
uv run pytest
uv run ruff check .
```
