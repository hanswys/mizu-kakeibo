# V0-2 Evaluation, 2026-10-15

Sanitized results of the [V0-2](ROADMAP.md#issue-v0-2-evaluate-the-expense-analyser) analyser evaluation. The dataset, full reports, and model outputs stay private, outside the repository. This note holds counts and generic error categories only.

## Dataset

- 33 real inbox messages received between 2026-08-19 and 2026-10-15, retrieved through the claude.ai Gmail connector as `RAW` messages. All 33 normalized without error.
- Ground truth was confirmed by the user before any model output was seen.
- Expected decisions under `expense-v0.4`: 6 `expense`, 6 `needs_review`, 21 `not_expense`.
- The expected expenses are food-delivery and ride-hailing receipts, an online-shopping order, and a software subscription receipt. The set has no bank or e-wallet transaction alerts, because the inbox receives none.
- Labelling conventions agreed with the user:
  - For delivery and ride-hailing apps, the merchant is the app, not the restaurant.
  - An order-placed notice for an order that also has a receipt is not an expense.
  - A failed payment is `not_expense`.

## Runtime

Ollama `0.23.1`, `qwen2.5:14b` at the pinned digest, temperature 0, seed 42, context 12288, 512 maximum generated tokens. The loaded model used 11.19 GiB, 95% on GPU, on a 16 GB Apple M2.

## Full run: `expense-v0.4`, 3 repeats

| Measure | Each run |
| --- | --- |
| Correct decisions | 27 of 33 |
| False expenses | 1 |
| Missed expenses | 0 |
| Expenses sent to review | 0 |
| Correct reviews | 5 of 6 |
| Unnecessary reviews | 5 |
| Missed reviews | 0 |
| Invalid outputs, operational failures, context overflows | 0 |
| Amount, currency, and date correct | 6 of 6 extracted expenses |
| Merchant correct | 1 of 6 |
| Exact transactions | 1 of 6 |

- **Consistency:** all 33 examples gave the same decision and the same output on every repeat.
- **Latency per call:** minimum 10.4 s, median 30.2 s, p95 118.4 s, maximum 200.0 s.
- **Tokens:** median prompt 1,127 and maximum 9,523. Median generated 55 and maximum 101.
- **Speed estimate:** the earlier figure of about 13 s per message came from short fictional messages. Real messages, especially link-heavy marketing, are slower.

### Error categories

- **False expense (1):** a subscription-offer confirmation whose charge was only in an attached receipt. The body mentioned only a future recurring fee, and the model reported that fee as a completed expense.
- **Merchant mismatch (5):**
  - Twice the model named the restaurant instead of the delivery app.
  - Once it named an ordered item instead of a merchant.
  - Twice it used a fuller legal or regional company name.

  The prompt does not state the merchant convention, so the mismatches are partly a labelling and prompt gap and partly genuine extraction errors.
- **Unnecessary review (5):** a failed payment, two subscription-cancellation notices, an exchange-rate alert, and a social-network message notification.

### Gmail Purchases membership

| Expected decision | In Purchases | Outside |
| --- | --- | --- |
| `expense` | 6 | 0 |
| `needs_review` | 6 | 0 |
| `not_expense` | 7 | 14 |

This sample overstates Purchases recall, because some candidate messages were found by searching the Purchases category. It is not evidence that a Purchases gate hides no real expenses.

### Other findings

- Two messages had a stub plain-text alternative, with the content only in HTML. The normalizer prefers plain text, so the analyser saw no content. This is the layout found for the job tracker's LinkedIn notifications, so it affects the shared adapter.
- A receipt in a bare `$` from a foreign company was recorded as `MYR` under the `expense-v0.4` rule. The user then changed the product rule to record the currency the message shows and to defer conversion to ringgit (see the [MVP](MVP.md)).

## Currency check: `expense-v0.5`, 6 messages, 3 repeats

This run used the bare-`$` receipt plus five messages with ringgit amounts or a bare `$`.

- With the revised prompt alone, the model still reported the bare-`$` receipt as an `MYR` expense on every repeat. The four ringgit receipts kept the correct amount, currency, and date.
- The contract therefore adds an application check (see [section 5](inbox-expense-contract.md#5-expense-analyser-output)). Against all 33 messages, given an `MYR` expense from the model, it changed only the bare-`$` receipt and the failed bare-`$` payment, and kept every ringgit receipt. A live call on the bare-`$` receipt returned `needs_review` with `currency` missing.
- Median latency was 25.7 s.

## Recommendation

`revise`, with four candidates:

1. Stop a future or recurring fee from being reported as a completed expense when the actual charge is only in an attachment.
2. State the merchant convention in the prompt, and decide whether merchant scoring should accept legal-form variants.
3. Reduce unnecessary reviews for cancellation notices, failed payments, rate alerts, and social notifications.
4. Decide whether the expense app should also prefer the HTML alternative for stub plain-text messages.

The user chose `revise` and asked for all four.

## Revision: `expense-v0.6`, 16 messages, 1 run

Changes:

- The [MVP](MVP.md) and [contract](inbox-expense-contract.md#version-0-decision-scope) now state the merchant convention, which messages are `not_expense` (failed payments, cancellations, rate alerts, social and account notifications), and that a future or recurring fee is never a completed expense. The prompt states the same rules as `expense-v0.6`. Merchant scoring is unchanged.
- The normalizer uses the HTML alternative when the plain-text alternative is a stub (see [normalization rule 2](inbox-expense-contract.md#2-normalized-inbox-message-envelope)). In this dataset that applies to exactly the two stub messages. This is shared infrastructure; see the job tracker's [J0-3](../jobs/ROADMAP.md#issue-j0-3-read-connector-messages-through-the-shared-normalizer).
- With its HTML visible, one stub message is a direct food order with a complete receipt. It was relabelled from `needs_review` to `expense`, so this run expects 5 expenses.

The run used the 12 messages the changes target (every decision error, every merchant mismatch, the bare-`$` receipt, and both stub messages) plus 4 randomly chosen others: a job alert, an expired membership, a cancellation, and an upcoming-payment notice expected as `needs_review`. One repeat was run, because every earlier run was fully consistent.

| Measure | `expense-v0.4` on these 16 | `expense-v0.6` |
| --- | --- | --- |
| Correct decisions | 8 of 16 | 15 of 16, and 16 of 16 with the extended check below |
| False expenses | 2 | 1, and 0 with the extended check |
| Unnecessary reviews | 5 | 0 |
| Missed expenses | 0 | 0 |
| Expenses sent to review | 1 | 0 |
| Amount, currency, and date correct | 4 of 4 | 5 of 5 |
| Merchant correct | 0 of 4 | 2 of 5 |

The `expense-v0.4` column re-scores the earlier run against the current labels; it saw the Domino's message as a stub and the bare-`$` receipt as an `MYR` expense.

- **Currency:** under `expense-v0.6` the model reported the bare-`$` receipt as `USD`, a currency the message never names, so the `MYR`-only check let it through. The check now covers every currency (see [section 5](inbox-expense-contract.md#5-expense-analyser-output)). Against all 33 messages, it keeps every ringgit receipt, and a model-reported `USD` would pass only for the exchange-rate alert, which names `USD`. A live call on the bare-`$` receipt returned `needs_review` with `currency` missing.
- **Merchant:** a ride receipt now names the app, and the legal and regional company names are gone. Two food-delivery receipts name the app's food service instead of the app, and one still names the restaurant.
- **Latency:** median 23.0 s, maximum 90.5 s. Median prompt tokens 1,293.

The user's `proceed`, `revise`, or `stop` decision on `expense-v0.6` is pending.
