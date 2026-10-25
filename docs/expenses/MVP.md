# Mizu Kakeibo MVP

## Document authority

This document is the source of truth for the MVP's product scope: the problem being solved, the user-visible workflow, the required capabilities, what is out of scope, and the definition of done.

It does not track implementation status or prescribe detailed schemas and invariants. Use the [delivery roadmap](ROADMAP.md) for sequencing, dependencies, and completion gates, and the [inbox-to-expense contract](inbox-expense-contract.md) for technical behavior.

## Product idea

Mizu Kakeibo watches the Gmail inbox for emails that may represent expenses. Every new inbox email is considered, so the user does not need to know which banks, merchants, or payment applications will send notifications in advance. The only exceptions are a fixed skip filter for mail that has not carried expenses: Gmail's Promotions and Social tabs and job-alert senders.

The system uses one full-message analyser to decide whether an email represents an expense. Confirmed expenses are written to a personal ledger, uncertain messages are held for review, and other messages are marked as processed without creating a transaction. Gmail remains the source of truth.

## Core workflow

```text
New Gmail inbox message
  -> Skip without analysis if it is in Promotions or Social,
     or comes from a job-alert sender
  -> Skip if the Gmail message ID was already processed,
     unless re-evaluation was explicitly requested
  -> Retrieve the full message
  -> Determine whether an expense occurred
      -> Expense: extract and log the transaction
      -> Not an expense: record the decision without a transaction
      -> Uncertain or incomplete: add the message to the review queue
```

Every new message that passes the skip filter follows the same path. There is no Gmail-label fast path or separate relevance model.

The skip filter was adopted on 2026-10-16 because analysing every message used most of a 16 GB laptop's memory for about a minute per message, and most messages were promotions and job alerts. In the 33-message evaluation set and the first 78 analysed inbox messages, no expense came from Promotions, Social, or a job-alert sender, and the only review item among them was a promotion too long to analyse. The trade-off is accepted: a receipt that Gmail files under Promotions or Social is never analysed. Promotions and Social are left out of the inbox search itself, which also keeps each sync's search smaller; they remain in their Gmail tabs. Skipped job alerts are reported in each sync's result.

## Message decisions

Every successfully processed Gmail message ends with one decision:

- `expense` means an expense was identified and a transaction was logged.
- `not_expense` means no transaction was logged.
- `needs_review` means the analyser could not decide safely or could not extract the required transaction values.

Version 0 focuses on clear completed expenses. Refunds, reversals, transfers, pending charges, bills, statements, and other policy-dependent cases remain review items; defining their permanent accounting treatment is deferred.

A message that reports no payment by the user is not an expense and does not need review: a failed or declined payment, a cancellation, an exchange-rate or price alert, a shipping or delivery update for an order, or a social or account notification. A future or recurring fee, such as the price after a trial ends, is never logged as a completed expense. A confirmation whose paid amount appears only in an attachment goes to review, and so does an order-placed notice that shows an order total but does not confirm payment, because the order's receipt may arrive separately and must not be logged twice.

Email text can try to instruct the analyser, so two checks in application code back up its decision. A message that shows a completed payment and an amount is never dropped as not an expense: it goes to review instead. An expense dated more than seven days before the day its email arrived, or more than a day after it, also goes to review rather than the ledger. Both were added on 2026-10-21, when the user asked to fix what a [prompt-injection test](prompt-injection-2026-10-21.md) found: a line added to a genuine receipt could hide it or change its date. The trade-off is accepted: a receipt that arrives more than a week after the payment, or a delivery update that repeats how the order was paid, becomes an unnecessary review. The [inbox-to-expense contract](inbox-expense-contract.md#5-expense-analyser-output) defines both checks.

The merchant is the business the user paid, named by its brand. When a delivery, ride-hailing, or other app takes the payment, the merchant is the app, not the restaurant, store, or driver.

Every expense is recorded in the currency the message shows and is never converted by the analyser. For this personal inbox, an amount with no currency marker at all is assumed to be Malaysian ringgit. A symbol shared by several currencies, such as a bare `$`, does not establish the currency on its own: unless the message states the currency elsewhere, the expense goes to review. The [inbox-to-expense contract](inbox-expense-contract.md) defines these rules.

Converting a foreign-currency expense to ringgit needs a real exchange rate, so it is application work rather than analyser work. It stayed deferred when [spending summaries](#spending-summaries) were added on 2026-10-18: the ledger keeps each expense's original amount and currency, and a summary totals each currency separately.

When an otherwise clear completed expense has no transaction date in the message, Version 0 uses the email's normalized received date. An explicit transaction date takes precedence. The [inbox-to-expense contract](inbox-expense-contract.md) defines the fallback and its limits.

## Minimum record

Every successful analysis retains the source provider and Gmail message ID, which together are the canonical source reference. It also retains the decision and sanitized reason, any missing fields, and the analyser versions needed for reproducibility. A transaction is stored only for an accepted expense. Its amount uses the currency's integer minor unit so the ledger does not depend on floating-point arithmetic.

The authoritative field definitions and invariants live in the [inbox-to-expense contract](inbox-expense-contract.md).

## MVP components

### Gmail retrieval

- Search new inbox messages.
- Start from a fixed date: inbox messages received before 2026-10-16 in Malaysia time are never processed. The user moved the start from 2026-10-10 to 2026-10-16, the day the skip filter and the lighter analyser model were released, so the remaining backlog of earlier messages is not processed; earlier decisions and ledger rows are kept.
- Process new messages in bounded batches, oldest first; the next sync continues with any that remain.
- Keep the Gmail message ID as the unique source identifier.
- Skip processed IDs unless the user explicitly requests re-evaluation.
- Leave Gmail's Promotions and Social tabs out of the search, and skip messages from job-alert senders without retrieving or analysing them, reporting them in the sync result.
- Retrieve the full content of every other new message before analysis.
- Normalize and analyse supported message-body text only; do not fetch or analyse attachments in Version 0.
- Preserve the Gmail message ID so the original message can be opened during review.
- Follow that contract for retrieval, normalization, analysis, validation, and persistence.

### Expense analyser

- Receive one normalized full message at a time.
- Return only `expense`, `not_expense`, or `needs_review`.
- Extract merchant, integer minor-unit amount, currency, and transaction date only for a confirmed expense.
- Return `needs_review` when the decision is uncertain, required values are missing, or necessary content cannot be interpreted safely.
- Send a message that is too long for the analyser to read in full to review, rather than leaving it to fail on every sync.
- Produce structured output that can be validated before anything is persisted.
- Follow the versioned model, prompt, and runtime configuration in the inbox-to-expense contract.
- Run light enough that the laptop stays usable during a sync, and release the model's memory soon after the sync ends.

On 2026-10-16 the user set speed and laptop load ahead of small accuracy gains: the analyser must not miss or falsely log expenses in evaluation, but an occasional unnecessary review or merchant-name error is acceptable for now and can be corrected in review. Improving accuracy further is deferred.

### Personal ledger

- Start with a private CSV file for manual validation.
- Store one row per confirmed expense.
- Store processed message decisions separately or alongside the ledger so non-expenses are not analyzed repeatedly.
- Allow corrections without changing the source email.

### Review

- Show the source email beside the proposed decision and transaction.
- Allow the user to accept an expense, mark it as not an expense, or correct extracted values.
- Preserve the Gmail message ID and the original decision after correction.

The user chose on 2026-10-18 to review in the terminal instead of on a browser page, because every other everyday task is a terminal command:

- `mizu-kakeibo review` lists the waiting messages, numbered, with each one's date, sender, subject, and why it needs review. The user picks one by its number.
- A picked message shows its sender, subject, received time, why it needs review, the values the analyser could not find, and the first lines of its text. The whole text and the Gmail message are each one key away.
- The user marks the message as an expense, marks it as not an expense, or skips it for now. For an expense, the merchant, currency, amount, and date are asked one at a time, with the sender's name and ringgit offered as defaults. Amounts are typed as a receipt shows them, such as `12.50`. The date has no default and must be typed; the date the email arrived is shown beside it only as a reminder. The user chose this on 2026-10-21, after a gym receipt sent on request weeks after the payment was accepted with Enter at the received date and counted in the wrong week: a message waits for review because its evidence was incomplete, so its arrival date cannot be trusted as the payment date.
- Nothing is saved until the user confirms, because a resolved message cannot be returned to review. After each message, the next waiting one is shown.

### Scheduled sync

Version 1 replaces the agent request with an hourly sync that runs by itself on the user's laptop. The user chose this design on 2026-10-16 after a [scheduler spike](scheduler-spike-2026-10-16.md).

- Read Gmail with read-only access through the user's own Google sign-in, kept privately on the laptop. The user chose on 2026-10-16 that the job tracker uses the same sign-in when it reads Gmail this way. A [Gmail API spike](gmail-api-spike-2026-10-16.md) showed that this finds and reads the same messages as the agent-driven sync.
- When the Gmail sign-in has expired or been removed, stop before reading any message and tell the user to sign in again. A sign-in should last without weekly renewal.
- Sync every hour from 08:00 to 23:00 Malaysia time, including after a sync earlier that day that left nothing to retry. The user chose this on 2026-10-21, replacing a rule that ended the day after the first such sync, because mail after the morning sync waited until the next day, so spending today was out of date and review items arrived a day late.
- Run only while the laptop is open and fully awake. An hour missed while the laptop is closed or asleep is caught up the next time it is open within those hours, so a day with the laptop closed throughout has no sync, and the next sync picks up its messages.
- Skip an hour when the laptop is on battery below 20%.
- Select, filter, and analyse messages exactly as the agent-driven sync does: the same skip filter, the same review for oversized messages, and at most 25 new messages per sync, with the rest continuing in the next hour. The user chose on 2026-10-16 to keep the 25-message batch even though reading Gmail directly removes its original reason, the agent's context, because it keeps a sync to a few minutes and the laptop usable.
- Never run two syncs at once. A sync that finds another one running, scheduled or started from chat, exits without doing anything.
- Send a message to review after its analysis fails in three syncs, instead of retrying it forever.
- After a sync that logged an expense, sent a message to review, or failed a message, show a notification with the counts of expenses, review items, and failures. When there are review items or failures, also show a dialog with a "Review in Terminal" button, which opens Terminal and starts `mizu-kakeibo review`. A sync with nothing new, or only non-expenses, stays silent. The user chose this on 2026-10-21 with the hourly sync, so ordinary mail does not show a notification every hour.
- When a sync stops before reading any message, because the Gmail sign-in, the analyser, or the Gmail search failed, show a dialog that says what failed, and for the sign-in how to sign in again, at most once a day and with no notification. The user chose this on 2026-10-17, so a problem lasting several hours does not notify every hour.
- Only a scheduled sync shows the notification and dialog.
- Keep a private record of each sync's message IDs, counts, and errors for 90 days, never message content.
- Keep the agent-driven sync as a manual fallback.
- Once the [job tracker's scheduled sync](../jobs/MVP.md#scheduled-sync) is built, each hourly run also syncs the job tracker, after the expense sync, as the user chose on 2026-10-23. The two share the hours, the laptop checks, and the Gmail sign-in and analyser checks, but not processing state. A job sync that is running or failing never stops the expense sync. The expense notification, dialogs, and record do not change.

## Spending summaries

The user asked on 2026-10-18, after Version 1, for a quick way to see what they have spent, because checking meant opening the ledger and remembering commands with long file paths.

- Running `mizu-kakeibo` on its own shows spending today, this week, and this month, how many messages wait for review, and the last scheduled sync, with the commands to see more.
- A spending command lists the expenses dated in a period with their total, or totals each merchant instead. A period is today, yesterday, this or last week, this or last month, this year, or a given day, month, or year.
- Each currency is totalled separately and never converted, so a period with a foreign-currency expense shows one total per currency.
- Reviewing starts with one command, `mizu-kakeibo review`, described under [Review](#review).
- In a terminal, the spending command numbers the expenses it lists and asks for one to correct, so a wrong merchant, amount, currency, or date is fixed without looking up the message ID. Pressing Enter quits. Totals by merchant are not numbered, because each row groups several expenses.
- A picked expense's merchant, currency, amount, and date are asked one at a time, as in [review](#review), with its logged values as the defaults. Nothing is saved until the user confirms, and the original decision is kept, as for any [correction](#personal-ledger). The user asked on 2026-10-18 for this to follow terminal review, and chose the same day to correct from the spending command itself rather than a separate one.
- A picked expense can instead be marked as not an expense, which removes it from the ledger and every total, such as a cost the user's employer pays back. Nothing is removed until the user confirms, and the original decision is kept. The user asked for this on 2026-10-21, when a company-claim gym receipt was counted as their own spending and the only fix was to change its values.
- Commands use the live database and ledger unless told otherwise, so everyday use needs no file paths.
- When changes have been merged to `main` since the installed copy was built, the summary says so and shows the commands to update it. The user asked for this on 2026-10-21, so updating does not depend on remembering it after each merge. Nothing updates itself.
- A summary covers what the latest sync has logged; mail that arrived since is not included until the next sync.
- Once the [job tracker](../jobs/MVP.md) has a database, the summary also shows how many job applications it tracks and how many of its emails wait for review, with its commands, so one command shows both apps. With the job tracker's [scheduled sync](../jobs/MVP.md#skim-list), it also shows how many job-related emails there are to skim this week, and with its [interview reminders](../jobs/MVP.md#interview-reminders), the next interview. This is the first surface the two projects share, chosen by the user on 2026-10-22 as a step toward combining them. Reading the counts never changes the job tracker or waits for its sync, and without a job database the summary is unchanged.

## Spending insights

The user asked on 2026-10-25 for the ledger to point out what needs attention, not only totals: subscriptions that were forgotten or became more expensive, and charges that look wrong. Both are worked out from the ledger in application code; the analyser is unchanged.

### Subscriptions

- A subscriptions command lists the merchants charged repeatedly at a regular interval, such as monthly or yearly, with each one's latest amount, interval, latest charge date, and expected next charge, and a monthly total for each currency.
- It points out a subscription whose latest charge differs from the one before, such as a price rise or a free trial that turned paid, and a subscription started this month.
- The spending summary adds one line when a subscription changed price or started this month.
- The list is worked out from the ledger each time, so nothing new is stored, and a corrected or removed expense changes it at once.
- A subscription is recognized from its charges, so it appears after its second charge. One paid through a card or wallet that sends no email appears once [spending without emails](#spending-without-emails) logs it.
- A merchant is a subscription when it was charged at least twice and every gap between consecutive charges fits one interval: 26 to 35 days for monthly, or 350 to 380 days for yearly. Its amounts may differ.
- Merchant names match when they are the same apart from letter case and spacing, with no fuzzier matching. Correcting an expense's merchant in the spending list joins a charge logged under another name.
- A price change is a latest charge whose amount or currency differs from the charge before. A subscription counts as started this month when its second charge, the one that makes it a subscription, is this month, since a first charge this month seldom has a second one yet.
- The expected next charge is one month or one year after the latest charge, on the same day or the month's last day. A subscription more than one interval past its expected charge is kept on the list and marked as possibly lapsed, and is left out of the monthly total.
- A receipt's own wording, such as a renewal date, does not mark a subscription after one charge, because that would change the analyser.

### Unusual and duplicate charges

- A logged expense is flagged as a possible duplicate when another expense has the same merchant, amount, and currency on the same day, and as unusual when its amount is far above what that merchant usually charges.
- A flag never changes or removes an expense. The expense is logged and counted as usual.
- After a scheduled sync that flagged an expense, the sync's notification says so, as it does for review items.
- The spending summary shows how many flagged expenses wait. For each, the user keeps it, which clears the flag, or marks it as not an expense, as in the spending list. A cleared flag does not return.
- The ledger records the transaction date, not its time, so a charge made twice within minutes is detected as twice on the same day.
- Settled in the roadmap's triage: how many earlier charges a merchant needs before an amount can be unusual, and how far above its usual amount; whether flags are resolved in `mizu-kakeibo review` or a list of their own; and whether expenses logged before the feature are flagged.

### History

Both insights need months of charges, and the ledger starts at 2026-10-16. The user may move the sync start earlier and backfill the ledger, after its size and run time are measured; without a backfill, both fill in over the coming months.

## Spending without emails

No payment-application integration is required when the application sends receipts or transaction notifications into Gmail. Those messages enter the inbox-wide workflow even when the sender is unknown and no Gmail filter exists.

Much of the user's spending sends no email: card alerts arrive by SMS or app push, and TNG eWallet sends no per-payment email. The user chose on 2026-10-25, after research on 2026-10-21, to turn those payments into emails the user sends themselves, so Gmail stays the only source and the existing workflow logs them:

- **UOB One credit card.** It sends an SMS for each transaction. An iPhone automation forwards each UOB transaction SMS to the user's Gmail.
- **Apple Pay taps.** The MAE Visa debit card works with Apple Pay. An iPhone Wallet automation emails each tap's merchant and amount to the user's Gmail. It covers taps in shops, not online card payments.
- **TNG eWallet.** Its app can email a transaction history with a PDF statement. Logging from it means reading an attachment and logging several expenses from one email, which the MVP excludes today. A TNG reload paid by the UOB One card already arrives as a UOB SMS. The roadmap's triage chooses between importing the statement and counting each reload as spending.

These rules apply to every capture route:

- A capture automation runs without a tap for each payment. A route that iOS will not run silently is reconsidered rather than relied on.
- A capture email carries only what the expense needs: the merchant, amount, currency, date, and which card or wallet. Never a full card number, a one-time password, or a balance.
- A payment is counted once even when a capture and the merchant's own receipt both report it. A likely pair goes to review or is flagged as a [possible duplicate](#unusual-and-duplicate-charges), never logged twice silently.
- Settled in the roadmap's triage: whether captures go through the analyser or a fixed-format rule, how the skip filter treats them, and how a capture and a receipt are paired.

Direct bank or payment-application connections, such as Bank Negara Malaysia's open finance, stay out of scope.

## Deferred improvements

These improvements wait until the MVP is complete:

- **Oversized messages.** Analyse a message that is too long for the analyser instead of sending it to review.
- **Sync performance.** Make each sync faster and lighter. The first real sync, on 2026-10-15, took about a minute per message, and most of the messages it analysed were job alerts and promotions. The skip filter and a smaller analyser model were pulled forward from this work on 2026-10-16.
- **Analyser accuracy.** Reduce unnecessary reviews and merchant-name errors once the end-to-end daily workflow is in use.
- **Analyser values in review.** Keep the values the analyser found for a message it sends to review, and offer them as the defaults when the user resolves it.

## Out of scope

The MVP does not require:

- A model-based metadata relevance gate. The skip filter is a fixed rule, not a model.
- Gmail labels or filters for financial senders.
- A complete definition of every financial event type before validation.
- Attachment retrieval or analysis, unless the TNG choice under [spending without emails](#spending-without-emails) adopts the statement import.
- Direct bank or payment-application connections.
- A dedicated forwarding address or inbound email provider. Capture emails are sent to the user's own Gmail.
- Categories, budgets, charts, or a visual dashboard. Spending is shown only as the terminal [spending summaries](#spending-summaries) and [spending insights](#spending-insights).
- Converting foreign-currency expenses to ringgit.
- Gmail push notifications instead of scheduled polling.
- Hosted deployment or PostgreSQL storage.
- Multi-user accounts, public onboarding, or native mobile applications.
- Subscription billing or a custom-trained model.

## Definition of done

The MVP is complete when one agent request:

1. Finds new inbox messages and retrieves each new message in full.
2. Does not reprocess Gmail message IDs already recorded.
3. Sends every new message that passes the skip filter through the same expense analyser.
4. Records each successful analysis as `expense`, `not_expense`, or `needs_review`.
5. Logs a traceable ledger row only for accepted expenses.
6. Presents uncertain decisions and missing transaction values for review.

Version 1 extends this so the same workflow runs by itself as the [scheduled sync](#scheduled-sync). The user accepted the MVP, including Version 1, as complete on 2026-10-18, and chose to find any remaining problems in everyday use; the [user guide](USER-GUIDE.md) describes that use.

The first validation run established the full-message baseline, which showed a meaningful latency problem; the skip filter is the response. Further metadata routing and Gmail labels may be evaluated later on the same terms.
