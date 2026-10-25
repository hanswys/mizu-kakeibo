# Job Tracker User Guide

How to use the job-application tracker day to day. It is part of the same `mizu-kakeibo` command as the expense app, and shares its installation, Ollama, and Gmail sign-in, described in the [expense user guide](../expenses/USER-GUIDE.md) and the [README](../../README.md). What the tracker does and why is in the [MVP](MVP.md); exact behaviour is in the [contract](job-tracker-contract.md).

## What it does for you

When you run a sync, the tracker reads your Gmail inbox messages received since 8 Oct 2026 and finds the ones that report the status of a job application: submitted, viewed, an interview, a rejection, or an offer. It keeps one application for each company and job title, with its current status. Emails it is unsure about, or that look like an application it already has under a slightly different name, wait for you in a review list. Job alerts, recommendations, and other mail change nothing.

Every hour from 08:00 to 23:00, the expense app's hourly sync also syncs the tracker, right after the expenses; see [the hourly sync](#the-hourly-sync). You can still sync by hand at any time.

Everything stays on the laptop, in `~/mizu-kakeibo-private/jobs/live/job-tracker.sqlite3`. The first sync creates it. It is separate from the expense ledger: the two apps read the same inbox but keep their own records, so one email can be both an expense and a job update.

## Syncing

The [hourly sync](#the-hourly-sync) does this for you. To sync now, run:

```sh
mizu-kakeibo jobs-sync
```

It reads up to 25 new messages, oldest first; add `--limit 50` for more in one run. It uses the expense app's read-only Gmail sign-in, so if `mizu-kakeibo sync` works, so does this.

The first syncs work through a backlog of the emails since 8 Oct 2026. Each run handles the oldest 25 and leaves the rest for later, listed as `deferred`. Run it again until nothing is deferred; after that, a sync handles only new emails and any that failed before.

It prints its result as JSON:

| Field | Means |
| --- | --- |
| `summary.status_update` | Emails that updated an application |
| `summary.not_application` | Emails that report no application status |
| `summary.review` | Emails waiting for you in [review](#reviewing-an-email) |
| `summary.failed` | Emails that could not be read or analysed; the next sync tries them again, and after three failures one goes to review |
| `summary.deferred` | New emails left for the next sync |
| `summary.skipped` | Emails already recorded by the time this sync stored them; nothing to do |
| `already_processed` | How many emails earlier syncs already handled |
| `applications` | How many applications this sync created, advanced, reopened, or left unchanged |
| `skim` | Job-related emails decided as not an application, by sender and subject |

Instead of reading `skim`, use [`jobs-skim`](#skimming-for-a-missed-update), which lists the same emails for the whole week.

Only one job sync runs at a time, by hand or hourly; another one started meanwhile prints `locked` and does nothing. A sync by hand never shows a notification.

## The hourly sync

The tracker syncs as the second step of the expense app's [hourly sync](../expenses/USER-GUIDE.md#a-normal-day), so it runs at the same hours, when the laptop is open, awake, and charged enough, and after the expenses are done:

1. **Every hour.** It reads up to 25 new emails, oldest first, exactly as `mizu-kakeibo jobs-sync` does. More wait for the next hour, and an email that failed is tried again then.
2. **The notification.** If it created, advanced, or reopened an application, found an email to review, or failed one, a notification titled `Mizu Kakeibo Jobs` says how many emails it checked, then for example `2 updates · 1 to review · 0 failed`. An hour with nothing new, or only emails that change no application, stays silent. No dialog opens: run `mizu-kakeibo jobs-review` when it suits you.
3. **Problems.** If the job sync could not start, for example because Gmail could not be searched, a `Job sync stopped` notification says so, at most once a day, and the next hour tries again. A problem with the Gmail sign-in or Ollama stops both apps and is shown once, by the expense app's dialog.

If you are running `jobs-sync` by hand when the hour comes, the hourly job step is skipped and the expenses still sync. A problem in either app never stops the other.

To see what happened each hour, read the end of the tracker's own log:

```sh
tail ~/mizu-kakeibo-private/jobs/live/scheduled.log
```

| `outcome=` | Meaning |
| --- | --- |
| `synced` | The job sync ran; the counts follow |
| `failed` | It stopped before reading any email; `stage=` says where |
| `locked` | Another job sync was already running |

Hours skipped because the lid was closed or the battery was low appear only in the expense app's `scheduled.log`. Each hour's details are kept for 90 days in `runs/` beside the log, as message IDs and counts only.

## Checking your applications

Once the first sync has run, `mizu-kakeibo` on its own adds a `Jobs` line under the expense summary, and the job commands under the expense ones:

```text
  Last sync   Fri 23 Oct 14:00 · 1 expense, 0 to review, 0 failed
  Jobs        2 applications · 1 to review · 3 to skim this week
```

The `to skim` count appears only when this week has emails to [skim](#skimming-for-a-missed-update).

To see the applications, run `mizu-kakeibo jobs-list`. They are listed by how far each got: offered, interview, viewed, applied, then rejected last, and within each status the most recently updated first:

```text
3 applications

     Updated     Status     Company         Job title
  1  2026-10-12  interview  Orchid Harbour  Product Designer
  2  2026-10-09  applied    Lumen Works     Backend Engineer
  3  2026-10-14  rejected   Kestrel Labs    Data Analyst

1 message to review: run mizu-kakeibo jobs-review
```

The company and job title are shown as first written. A status only moves forward, from applied to viewed to interview to offered or rejected, so a late or repeated email does not move an application back. Applying again after a rejection reopens it as applied.

The numbers follow the order, so an application's number can change when another is added or a status changes. Run `jobs-list` again before `jobs-show` if a sync may have run since.

## Looking at one application

Before a call or an interview, run `mizu-kakeibo jobs-show` with the application's number from `jobs-list`, such as `mizu-kakeibo jobs-show 1`. It shows the application, the other names it was confirmed under in review, and every email linked to it, oldest first, with the status each reported and what it did to the application:

```text
  Company    Orchid Harbour
  Job title  Product Designer
  Status     interview, updated 2026-10-12
  Aliases    Orchid Harbour Group · Product Designer

  1  Fri  9 Oct  Example Careers  applied    created              Orchid Harbour received your application
  2  Mon 12 Oct  Example Hiring   interview  advanced (reviewed)  Interview for Product Designer

  2 emails

Number to open in Gmail, or Enter or q to quit:
```

An email either `created` the application, `advanced` it to a further status, `reopened` it after a rejection, or left it `unchanged`; `(reviewed)` marks an email you attached in review. Type a number to open that email in Gmail, as often as you like, and press Enter to finish. Nothing is recorded, and it works while a sync is running.

## Skimming for a missed update

Run `mizu-kakeibo jobs-skim`. It lists this week's emails from LinkedIn, Jobstreet, and hiring systems, or with an application or interview in the subject, that the tracker decided report no status, oldest first:

```text
This week, Mon 19 Oct – Sun 25 Oct 2026

  1  Mon 19 Oct  LinkedIn        Analyst roles for you
  2  Wed 21 Oct  Example Hiring  Hiring update

  2 emails to skim

Number to open in Gmail, or Enter or q to quit:
```

Those emails are not reviewed, so a missed status update would otherwise go unnoticed. Type a number to open that email in Gmail, as often as you like, and press Enter to finish. Nothing is recorded. If one is a real status update, ask Claude Code in this repository to re-evaluate it.

It takes the same periods as `mizu-kakeibo spend`, such as `jobs-skim last-week` or `jobs-skim 2026-10`.

## Reviewing an email

Run `mizu-kakeibo jobs-review`. It lists the waiting emails, numbered, with why each one waits:

```text
2 messages to review

  1  Sat 10 Oct  Orchid Harbour Group  Interview for Product Designer  likely match
  2  Tue 13 Oct  Example Careers       Thanks for applying             missing company, job title

Number to review, or q to quit:
```

| Why | Means |
| --- | --- |
| `missing ...` | The email lacks the company, job title, or status, or the analyser could not read it safely |
| `likely match` | It looks like an application you already have, under a slightly different company name or job title |
| `contradicts the outcome` | It reports an offer for a rejected application, or a rejection for an offered one |

Type a number to see that email: its sender, subject, received time, what the tracker extracted, any application it suggests, and its first 15 lines. Then press a key and Enter:

| Key | Does |
| --- | --- |
| `a` | Accepts the suggestion: attaches the email to the suggested application. Only offered when there is one. |
| `e` | Lets you type the company, job title, and status, and matches them to an application or creates one. If they only look like an application you have, nothing is saved and it suggests `t` or `c`. |
| `t` | Attaches it to another application, picked by number from your list |
| `c` | Creates a new application from a company, job title, and status you type |
| `n` | It is not an application |
| `m` | Shows the whole email; press `q` to come back |
| `g` | Opens the email in Gmail |
| `s` | Skips it for now |
| `q` | Quits |

When asked for a value, press Enter to take the one in brackets. A status can be typed whole or by its first letter, such as `i` for interview. Nothing is saved until you answer `y` to `Save?`, because a resolved email cannot go back to review. Quitting, `Ctrl-C`, or closing the window saves nothing for the email on screen.

When you attach an email whose company or job title is written differently, such as `Orchid Harbour Group` for `Orchid Harbour`, the tracker remembers that name for the application, so later emails written the same way match it without review.

## When something goes wrong

| You see | What to do |
| --- | --- |
| The Gmail sign-in has expired or was removed | Run `mizu-kakeibo gmail-sign-in --replace` and approve read-only access in the browser. The sign-in is shared, so this fixes the expense sync too. |
| An error about Ollama | Check that Ollama is running, as for the expense app; see the [README](../../README.md#setup) |
| `no job-tracker database` from `jobs-list`, `jobs-show`, `jobs-review`, or `jobs-skim` | Run `mizu-kakeibo jobs-sync` first; the first sync creates it |
| `no application 7` from `jobs-show` | Run `mizu-kakeibo jobs-list` and use a number it shows; the numbers can change after a sync |
| A `Job sync stopped` notification | Usually nothing; it tries again next hour. If it repeats, run `mizu-kakeibo jobs-sync` to see the error, or look at `~/mizu-kakeibo-private/live/launchd.err.log` after an unexpected error. |
| `Jobs  could not read the job database` in the summary | Run `mizu-kakeibo` again. If it stays, ask Claude Code in this repository to investigate. |

Keep `~/mizu-kakeibo-private/` backed up if your application history matters to you; it is never committed to the repository.
