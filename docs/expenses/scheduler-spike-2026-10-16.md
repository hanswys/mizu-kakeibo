# Scheduler Spike, 2026-10-16

Evidence for the [scheduled sync](MVP.md#scheduled-sync) on the user's 16 GB M2 laptop running macOS 27. Every test used stub commands and scratch databases; no Gmail message, live database, or ledger was touched. The test LaunchAgents were removed afterwards.

## Schedule and sleep

- A LaunchAgent with `StartCalendarInterval` fired at the set minute, four to five seconds late with `ProcessType` `Background`. Its `PATH` was `/usr/bin:/bin:/usr/sbin:/sbin`.
- launchd ran no second instance of a running job, and `launchctl kickstart` on it did nothing.
- The system's `newsyslog` job runs at minute 30 of each hour. Over the previous 24 hours, launchd started it outside minute 30 sixteen times, each once at the first wake after the missed time: thirteen times at the very second of a lid-closed maintenance wake lasting about ten seconds, three times after a full wake. Missed hours in between were not run separately.
- No user LaunchAgent with a calendar trigger existed overnight, so whether one also fires in a maintenance wake was not observed. The full-wake gate makes the design correct either way.
- A scheduled wake through `pmset` needs administrator rights and was not used.

## Overlap and locking

- Two processes ran `InboxProcessor` on the same ten messages against one scratch database, with a stub analyser taking 0.3 seconds a message. Neither stored a message twice, and the ledger had ten rows, but each made ten model calls.
- Two overlapping re-evaluations of the same ten messages each stored a revision, leaving revision 3 with one active analysis and one ledger row per message.
- Two processes opening a new database at the same moment failed with `database is locked` in 14 of 40 opens; with an existing database, none of 40 failed. `PRAGMA journal_mode = WAL` on a new file does not wait for the busy timeout.
- An `fcntl.flock` lock taken without waiting made a second run exit cleanly in both orders, and a holder killed with `SIGKILL` released it.

## Notifications from launchd

- An `osascript` notification was delivered, attributed to Script Editor, and held in Notification Center by the active Focus. Clicking it opens Script Editor, and it cannot carry buttons.
- An `osascript` dialog was shown, and its `Open review list` button opened a local page in the browser.
- `terminal-notifier` could not be installed: Homebrew has no build for macOS 27, and building it needs Xcode.

### Dialogs after the run exits, 2026-10-17

A follow-up for [V1-4](ROADMAP.md#issue-v1-4-report-each-scheduled-sync-and-open-the-review-list), with three stub LaunchAgents whose run started an `osascript` dialog and exited at once. The test LaunchAgents were removed afterwards.

- A dialog started as an ordinary child process was killed as soon as the run exited, because launchd ends the job's remaining process group.
- A dialog started in a new session stayed up after the run exited. launchd reported the job as not running, and starting it again while the dialog was up ran a second instance normally, so a waiting dialog does not hold the next hour.
- An ordinary child also survived with `AbandonProcessGroup` set, but that needs a plist change.
- Clicking `Open review list` in a dialog that outlived its run ran `open` on the page without error, from the dialog's own AppleScript.

## Retry by hour

A stub wrapper with hourly triggers, the lock, and a done-today gate failed in its first hour, succeeded in the next, and skipped the third.
