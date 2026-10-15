---
name: expense-sync
description: Run one Mizu Kakeibo agent-driven sync. Find new Gmail inbox messages through the claude.ai Gmail connector, process them with the local analyser, and report expenses, review items, and skipped, failed, and deferred messages. Use when the user asks to sync, process, or check new inbox expenses.
---

# Expense sync

One sync follows the connector sync in [the contract](../../../docs/expenses/inbox-expense-contract.md#connector-sync). Run every command from the repository root so `select-new` and `collect-raw` find this session's transcript.

Real message content, message IDs, the database, the ledger, and run files are private. Keep them under `~/mizu-kakeibo-private/live/` and never in the repository. Do not quote message bodies in chat; refer to messages by sender, subject, and Gmail link only when the user is reviewing them.

Shell variables do not persist between commands, and zsh does not split a variable into several arguments, so write the storage flags out in every command that needs them:

```sh
--database ~/mizu-kakeibo-private/live/kakeibo.sqlite3 --ledger ~/mizu-kakeibo-private/live/ledger.csv
```

Below, `STORAGE` stands for those flags and `RUN` for one new directory, `~/mizu-kakeibo-private/live/runs/<YYYYMMDD-HHMMSS>`, created at the start and used for every step of the sync.

1. **Check the runtime.** `curl -s http://127.0.0.1:11434/api/version` must report `0.23.1`. If nothing answers, start the pinned server with `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mizu-kakeibo.ollama.plist` and check again. If the version differs or the server still does not answer, stop and tell the user.
2. **Search.** Run `uv run mizu-kakeibo search-query STORAGE`. Add `--full` when the newest `~/mizu-kakeibo-private/live/runs/*/result.json` lists failed IDs. Call `search_threads` with the printed `query`, `pageSize` 50, and `view` `THREAD_VIEW_METADATA_ONLY`, and repeat with `pageToken` until no `nextPageToken` is returned.
3. **Select.** Run `uv run mizu-kakeibo select-new --output RUN/selection.json STORAGE`. Add `--limit N` only when the user asks for a smaller batch. If `incomplete_thread_ids` is not zero, call `get_thread` with `messageFormat` `METADATA_ONLY` for each listed thread and run `select-new` again. If `message_ids` is zero, report that there is nothing new and stop.
4. **Fetch.** For every ID in `message_ids` of `RUN/selection.json`, call `get_message` with `messageFormat` `RAW`. Several calls may run in parallel. Never use another format, and do not read or summarize the returned content.
5. **Collect.** Run `uv run mizu-kakeibo collect-raw RUN/selection.json --output RUN/messages.json`. If it reports `missing` IDs, fetch those again with `get_message(RAW)` and repeat this step.
6. **Process.** Run `uv run mizu-kakeibo process RUN/messages.json --selection RUN/selection.json STORAGE > RUN/result.json` in the background. Each message takes about 30 seconds. The command refuses to run when Ollama or the model does not match the contract; report that to the user instead of working around it.
7. **Report.** From `summary` in `RUN/result.json`, give the count of each group and the failed IDs with their sanitized errors. For new expenses, show the matching rows of `~/mizu-kakeibo-private/live/ledger.csv`. List review items with `uv run mizu-kakeibo review-list STORAGE`, including each `gmail_url`. When `deferred` is not empty, say how many messages remain and that the next sync continues with them.

Resolve a review item only on the user's instruction, with `uv run mizu-kakeibo review-resolve gmail MESSAGE_ID DECISION STORAGE`, adding `--merchant`, `--amount-minor`, `--currency`, and `--transaction-date` for an expense. Failed messages stay unprocessed, and the next sync retries them with a full search.
