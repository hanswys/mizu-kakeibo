# Mizu Kakeibo User Guide

How to use Mizu Kakeibo day to day once it is installed. Installation and every command's options are in the [README](../../README.md); what the app does and why is in the [MVP](MVP.md); exact behaviour is in the [contract](inbox-expense-contract.md#9-scheduled-run).

## What it does for you

Every hour from 08:00 to 23:00, your laptop reads new Gmail inbox messages, decides which are completed expenses, and logs each one as a row in your private ledger. Messages it is unsure about wait for you in a review list. You do not need to open a chat or label anything in Gmail.

Everything stays on the laptop, under `~/mizu-kakeibo-private/live/`:

| File | What it is |
| --- | --- |
| `ledger.csv` | Your expenses: merchant, amount in the currency's smallest unit (`2400` MYR is RM24.00), currency, and date, one row per Gmail message |
| `review.command` | Starts `mizu-kakeibo review` in Terminal when you click **Review in Terminal** |
| `kakeibo.sqlite3` | Every decision the app has made; you never need to open it |
| `scheduled.log` | One line per hourly attempt, for troubleshooting |

## A normal day

1. **The first sync of the day.** At the first hour from 08:00 when the laptop is open and awake, the sync reads up to 25 new messages. Promotions, social updates, and job alerts are skipped without being read.
2. **The notification.** If it analysed anything, a notification says how many messages it checked and how many were expenses, to review, or failed, for example `1 expense · 2 to review · 0 failed`. macOS shows it as coming from Script Editor, and a Focus mode may hold it in Notification Center. A sync with nothing new stays silent.
3. **The dialog.** If anything needs review or failed, a dialog also appears. Click **Review in Terminal** to resolve the messages now, or **Dismiss** to deal with them later. It closes by itself after an hour.
4. **The rest of the day.** Once a sync finishes with no failures and nothing left over, later hours do nothing until tomorrow. Mail that arrives after that is picked up the next morning. If a sync failed or left messages for later, the next hour continues.

A sync is skipped, and tried again the next hour, while the lid is closed, the laptop is asleep, or it is on battery below 20%. An hour missed that way runs when you next open the laptop, or at the next hour.

## Checking your spending

Open Terminal and run `mizu-kakeibo` on its own:

```text
Mizu Kakeibo · Sun 18 Oct 2026

  Today       MYR 0.00 · no expenses
  This week   MYR 84.36 · 3 expenses
  This month  MYR 131.16 · 5 expenses
  To review   nothing
  Last sync   Sun 18 Oct 01:38 · 1 expense, 0 to review, 0 failed
```

To see the expenses behind a total, use `mizu-kakeibo spend` with a period. Add `--by merchant` to total each merchant instead.

| Command | Shows |
| --- | --- |
| `mizu-kakeibo spend` | Today's expenses |
| `mizu-kakeibo spend yesterday` | Yesterday's |
| `mizu-kakeibo spend week` | This week's, Monday to Sunday; `last-week` for the one before |
| `mizu-kakeibo spend month` | This month's; `last-month` for the one before |
| `mizu-kakeibo spend year` | This year's |
| `mizu-kakeibo spend 2026-10-11` | One day; `2026-10` for a month, `2026` for a year |
| `mizu-kakeibo spend month --by merchant` | This month's total for each merchant |

An expense counts on its transaction date. Totals include only what the latest sync logged, so mail from the last hour may not be counted yet. An expense in another currency is totalled on its own line, never converted to ringgit.

You can also ask Claude Code in this repository, for example "how much did I spend on Grab this month?".

## Reviewing a message

Run `mizu-kakeibo review`, or click **Review in Terminal** in the dialog. It lists the waiting messages, numbered:

```text
2 messages to review

  1  Sat 17 Oct  Shopee         Your order is confirmed  missing amount
  2  Sun 18 Oct  Example Bank   Payment notice           missing merchant, date

Number to review, or q to quit:
```

Type a number to see that message: its sender, subject, received time, why the app was unsure, and its first 15 lines. Then press a key and Enter:

| Key | Does |
| --- | --- |
| `e` | It is an expense. You are asked for the merchant, currency, amount, and date in turn; press Enter to take the value in brackets. Type the amount as the receipt shows it, such as `35.90`. |
| `n` | It is not an expense |
| `m` | Shows the whole message; press `q` to come back |
| `g` | Opens the message in Gmail |
| `s` | Skips it for now |
| `q` | Quits |

Nothing is saved until you answer `y` to `Save?`, because a resolved message cannot go back to review. After each message the next one is shown, and after the last the list is shown again. Quitting, `Ctrl-C`, or closing the window saves nothing for the message on screen.

You can also ask Claude Code in this repository, for example "resolve the Shopee review item as an expense of RM35.90 on 18 October".

## Fixing a logged expense

If a ledger row has the wrong merchant, amount, currency, or date, ask Claude Code, for example "correct the Grab expense on 17 October to RM26.00", or run:

```sh
mizu-kakeibo ledger-correct gmail MESSAGE_ID --amount-minor 2600
```

Give only the values to change. The message ID is the second column of `ledger.csv`. The original decision is kept, so a correction can always be traced.

## When something goes wrong

The app tells you with a dialog, at most once a day, when a sync could not start:

| Dialog says | What to do |
| --- | --- |
| The sign-in has expired or was removed | Run `mizu-kakeibo gmail-sign-in` in Terminal and approve read-only access in the browser |
| It couldn't start the analyser (Ollama) | Usually nothing; it tries again next hour. If it repeats, see the Ollama setup in the [README](../../README.md#setup) |
| It couldn't search Gmail | Usually a network problem; it tries again next hour |
| An unexpected error | Look at `~/mizu-kakeibo-private/live/launchd.err.log`, or ask Claude Code to investigate |

A message that fails is retried in later hours, and goes to the review list after it fails three times.

To see what happened each hour, read the end of the log:

```sh
tail ~/mizu-kakeibo-private/live/scheduled.log
```

| `outcome=` | Meaning |
| --- | --- |
| `synced` | A sync ran; the counts follow |
| `done_today` | Today's sync already finished |
| `not_full_wake` | The lid was closed or the laptop was not fully awake |
| `low_power` | On battery below 20% |
| `locked` | Another sync was already running |
| `failed` | The sync stopped before reading any message; `stage=` says where |

## Running a sync yourself

- **Right now, as the schedule would:** `launchctl kickstart gui/$(id -u)/com.mizu-kakeibo.sync`. It does nothing if today's sync has already finished, and a sync after midnight counts for the new day.
- **Without notifications, and without finishing the day:** `mizu-kakeibo sync`.
- **From a chat, as a backup:** ask Claude Code in this repository to run `/expense-sync`. It reads Gmail through the claude.ai connector instead of the app's own sign-in.

Only one sync runs at a time; another one started meanwhile exits without doing anything.

## Updating, pausing, and removing

- **Update** after new changes are merged to `main`:

  ```sh
  git -C ~/conductor/repos/mizu-kakeibo pull
  uv tool install --reinstall ~/conductor/repos/mizu-kakeibo
  ```

- **Pause** the hourly sync: `launchctl bootout gui/$(id -u)/com.mizu-kakeibo.sync`
- **Resume** it: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mizu-kakeibo.sync.plist`
- **Remove** it completely: see the scheduled sync section of the [README](../../README.md#scheduled-sync).

Keep `~/mizu-kakeibo-private/` backed up if the ledger matters to you; it is never committed to the repository.
