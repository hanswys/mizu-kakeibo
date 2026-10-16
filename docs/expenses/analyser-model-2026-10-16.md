# Analyser Model Evaluation, 2026-10-16

Sanitized results of the comparison that replaced `qwen2.5:14b` with a smaller analyser model for [V0.5-4](ROADMAP.md#issue-v05-4-switch-to-a-lighter-analyser-model). The dataset, full reports, and model outputs stay private, outside the repository. This note holds counts and generic error categories only.

## Why

The 14b model used 11.2 GiB of a 16 GB laptop's memory and took a median 26 s per message, which left the laptop hard to use during a sync. The user set speed and laptop load ahead of small accuracy gains, provided the model makes no false or missed expenses (see the [MVP](MVP.md#expense-analyser)).

## Dataset and runtime

- The 33-message private set from the [V0-2 evaluation](v0-2-evaluation-2026-10-15.md), with its final labels: 6 `expense`, 5 `needs_review`, 22 `not_expense`. The labels did not change; the dataset was re-recorded for `expense-v0.9` because the new prompt rules match how those messages were already labelled.
- Candidates ran on Ollama `0.40.5`, because the `qwen3.5` models need a newer Ollama than `0.23.1`, with thinking disabled, temperature 0, seed 42, context 12288, and 512 maximum generated tokens, on a 16 GB Apple M2.

## Prompt `expense-v0.9`

Two rules were added to `expense-v0.6`, both following existing labels:

- A shipping, dispatch, or delivery update for an order is `not_expense`, even when it repeats the order total.
- An order-placed notice that shows an order total but no payment method, paid amount, or receipt goes to review, because its receipt may arrive separately.

`expense-v0.8` had only the second rule; on it the 9b model sent a shipping update to review.

## Comparison, 1 run each

| Model | Prompt | Correct decisions | False / missed expenses | Merchant correct | Median latency | Max latency | Memory |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `qwen2.5:14b` (previous) | `expense-v0.6` | 33 | 0 / 0 | 5 of 6 | 26.2 s | 163 s | 11.2 GiB |
| `qwen3.5:9b` (MLX default tag) | `expense-v0.6` | 31 | 1 / 0 | 6 of 6 | 17.6 s | 89 s | 13.0 GiB and growing |
| `qwen3.5:9b-q4_K_M` | `expense-v0.8` | 32 | 0 / 0 | 6 of 6 | 14.5 s | 87 s | 4.7 GiB |
| `qwen3:4b-instruct-2507-q4_K_M` | `expense-v0.9` | 30 | 0 / 0 | 4 of 6 | 10.2 s | 68 s | 4.2 GiB |
| **`qwen3.5:4b-q4_K_M`** | `expense-v0.9` | **32** | **0 / 0** | **5 of 6** | **7.7 s** | **52 s** | **3.3 GiB** |

- Amount, currency, and date were correct for all 6 expected expenses with every model.
- The MLX build's memory grew from 7.1 to 15.4 GiB during its run, so models are pinned by their GGUF quantization tag.
- On a 16-message subset, `qwen3.5:9b-q4_K_M` with `expense-v0.9` was correct on all 16.

## Adoption run: `qwen3.5:4b-q4_K_M`, `expense-v0.9`, 3 repeats

Run through the analyser's own pinned path on the `expense-sync` server.

| Measure | Each run |
| --- | --- |
| Correct decisions | 32 of 33 |
| False, missed, and sent-to-review expenses | 0 |
| Correct reviews | 5 of 5 |
| Unnecessary reviews | 1 |
| Missed reviews | 0 |
| Invalid outputs, operational failures, context overflows | 0 |
| Amount, currency, and date correct | 6 of 6 |
| Merchant correct | 5 of 6 |
| Decisions and outputs consistent across runs | 33 of 33 |

- The unnecessary review is a return notice whose refund has not been issued, which the user labelled `not_expense`.
- The merchant mismatch is a pizza-chain receipt whose brand name differs from the label; it can be corrected in review.
- **Latency per call:** median 13.0 s, p95 27.7 s, maximum 93.8 s; a macOS background media-analysis process was using more than one CPU core during this run, roughly halving generation speed compared with the 1-run comparison. Median prompt tokens 1,430, maximum 9,768; median generated tokens 66.
- **Memory:** 3.29 GiB, all on GPU.

## Decision

Adopt `qwen3.5:4b-q4_K_M` with `expense-v0.9` on Ollama `0.40.5`, recorded on 2026-10-16. It makes no false or missed expenses, uses about 70% less memory than the 14b, and is about three times faster when the laptop is otherwise idle. Known limits, none blocking:

- One `not_expense` message goes to review, and one merchant name differs from the label.
- The set is small and has no bank or e-wallet alerts.
- Improving accuracy further is deferred (see the [MVP](MVP.md#deferred-improvements)).
