# Mizu Kakeibo

Mizu Kakeibo is a local, contract-driven inbox-to-expense processor. The product
scope lives in [`docs/expenses/MVP.md`](docs/expenses/MVP.md), and the
implemented technical behavior lives in
[`docs/expenses/inbox-expense-contract.md`](docs/expenses/inbox-expense-contract.md).

## Projects

- Expenses (Mizu Kakeibo): [`docs/expenses/MVP.md`](docs/expenses/MVP.md)
  - Day-to-day use: [`docs/expenses/USER-GUIDE.md`](docs/expenses/USER-GUIDE.md)
- Job-application tracker (planning): [`docs/jobs/MVP.md`](docs/jobs/MVP.md)

The setup and commands below apply to the expense project.

## Setup

```sh
uv sync --extra dev
```

The analyser requires Ollama `0.40.5` with the exact `qwen3.5:4b-q4_K_M` build named in
the contract. Data files passed to the commands below are private and must remain
outside version control.

Homebrew and the Ollama desktop app track newer releases, so install the pinned
command-line build instead (macOS):

```sh
mkdir -p ~/.local/opt/ollama-0.40.5 && cd ~/.local/opt/ollama-0.40.5
curl -fsSL https://github.com/ollama/ollama/releases/download/v0.40.5/ollama-darwin.tgz | tar -xz
ln -sf ~/.local/opt/ollama-0.40.5/ollama ~/.local/bin/ollama
ollama serve &            # or run it from a launchd user agent
ollama pull qwen3.5:4b-q4_K_M
```

The commands refuse to run if the Ollama version or model digest differs from the
contract.

## Commands

### Sync

Ask Claude Code to run the `expense-sync` skill (`/expense-sync`). It searches the
inbox through the claude.ai Gmail connector with the query `search-query` prints,
then runs the commands below from the
repository root. `select-new` and `collect-raw` read the connector results from the
current Claude Code session transcript, as described in the contract's connector
sync section:

```sh
uv run mizu-kakeibo search-query \
  --database private-kakeibo.db --ledger private-ledger.csv
uv run mizu-kakeibo select-new --output private-run/selection.json \
  --database private-kakeibo.db --ledger private-ledger.csv
uv run mizu-kakeibo collect-raw private-run/selection.json \
  --output private-run/messages.json
uv run mizu-kakeibo process private-run/messages.json \
  --selection private-run/selection.json \
  --database private-kakeibo.db --ledger private-ledger.csv
```

`process` prints a summary of expense, not-expense, review, skipped, failed, and
deferred message IDs, followed by the result for each message.

### Gmail API sync

Version 1 reads Gmail through the Gmail API instead of an agent, as described in
the contract's Gmail API retrieval section. Save the Desktop OAuth client's JSON as
`~/mizu-kakeibo-private/gmail-api/client_secret.json`, then sign in once in the
browser with read-only access:

```sh
uv run mizu-kakeibo gmail-sign-in
```

The token is written beside the client secret with mode `0600`. A sign-in that
still works is kept unless you add `--replace`. One command then searches,
selects, fetches, and processes new inbox messages with no agent and no browser:

```sh
uv run mizu-kakeibo sync \
  --database private-kakeibo.db --ledger private-ledger.csv
```

It prints the same summary and results as `process`. Add `--full` after a sync
with failed messages, and `--limit N` for a smaller batch. When the sign-in has
expired or been removed, it stops before reading Gmail and asks you to sign in
again.

### Scheduled sync

The LaunchAgent `com.mizu-kakeibo.sync` runs `scheduled-sync` at minute 0 of each
hour from 08:00 to 23:00, as described in the contract's scheduled run section. It
runs a copy installed with `uv tool install`, never a checkout, so install it from
an up-to-date `main`:

```sh
git -C ~/conductor/repos/mizu-kakeibo pull --ff-only
uv tool install --reinstall ~/conductor/repos/mizu-kakeibo
~/.local/bin/mizu-kakeibo launch-agent \
  --database ~/mizu-kakeibo-private/live/kakeibo.sqlite3 \
  --ledger ~/mizu-kakeibo-private/live/ledger.csv \
  > ~/Library/LaunchAgents/com.mizu-kakeibo.sync.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mizu-kakeibo.sync.plist
```

To update it later, pull `main` and run the `uv tool install --reinstall` line
again; the LaunchAgent does not change. Each run appends one line to
`~/mizu-kakeibo-private/live/scheduled.log`, and a run that passes the gates writes
`run.json` in its own directory under `runs/`. After a run that analysed or failed a
message, a notification shows its counts, and review items or failures also open a
dialog whose `Open review list` button opens `~/mizu-kakeibo-private/live/review.html`.
A run that fails before reading any message, for example because the Gmail sign-in
expired, shows one dialog a day saying what to do. To run it now instead of waiting for
the hour:

```sh
launchctl kickstart gui/$(id -u)/com.mizu-kakeibo.sync
```

To remove it:

```sh
launchctl bootout gui/$(id -u)/com.mizu-kakeibo.sync
rm ~/Library/LaunchAgents/com.mizu-kakeibo.sync.plist
uv tool uninstall mizu-kakeibo
```

### Individual steps

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
persist the decisions. Add `--selection` with a `select-new` or `filter-new` output
to include its skipped and deferred IDs in the summary:

```sh
uv run mizu-kakeibo process private-envelopes.json \
  --database private-kakeibo.db --ledger private-ledger.csv
```

Only one `sync` or `process` runs at a time for a database: a run that finds another
holding the lock file beside the database records nothing, prints `status` `locked`,
and exits successfully. A message whose analysis times out or returns invalid output
three times goes to review instead of being retried.

Use `--reevaluate` to explicitly replace the active analysis for an already
processed source. Review items can be listed, each with a link to its Gmail message,
and resolved without modifying the source message:

```sh
uv run mizu-kakeibo review-list --database private-kakeibo.db
uv run mizu-kakeibo review-resolve gmail MESSAGE_ID not_expense \
  --database private-kakeibo.db --ledger private-ledger.csv
```

For an accepted or corrected expense, add `--merchant`, `--amount-minor`,
`--currency`, and `--transaction-date` to `review-resolve`. Every `sync`, `process`,
and `review-resolve` also rewrites the private page `review.html` beside the database,
listing the unresolved review items with their Gmail links.

Correct an expense that is already logged, giving only the fields to change. The
original analysis is kept, the correction is recorded, and the ledger row is
rewritten:

```sh
uv run mizu-kakeibo ledger-correct gmail MESSAGE_ID --merchant "Example Delivery" \
  --database private-kakeibo.db --ledger private-ledger.csv
```

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
