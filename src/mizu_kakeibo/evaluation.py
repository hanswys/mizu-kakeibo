from __future__ import annotations

import json
import statistics
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import httpx
from pydantic import Field, model_validator

from .constants import PROMPT_VERSION
from .models import (
    AnalysisDecision,
    ContractModel,
    ExpenseAnalysisInput,
    InboxMessage,
    Transaction,
)
from .normalization import parse_inbox_message
from .ollama import AnalysisError, ContextOverflowError, TokenUsage
from .processor import Analyser

TRANSACTION_FIELDS = ("merchant", "amount_minor", "currency", "transaction_date")


class ExpectedOutcome(ContractModel):
    decision: AnalysisDecision
    transaction: Transaction | None

    @model_validator(mode="after")
    def transaction_only_for_expense(self) -> ExpectedOutcome:
        if (self.decision is AnalysisDecision.EXPENSE) != (self.transaction is not None):
            raise ValueError("expected transaction is required for expense and only for expense")
        return self


class EvaluationExample(ContractModel):
    message: InboxMessage
    expected: ExpectedOutcome
    gmail_purchases: bool | None = None


class EvaluationDataset(ContractModel):
    dataset_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    examples: list[EvaluationExample] = Field(min_length=1)

    @model_validator(mode="after")
    def message_ids_must_be_unique(self) -> EvaluationDataset:
        ids = [example.message.source.message_id for example in self.examples]
        if len(ids) != len(set(ids)):
            raise ValueError("evaluation message IDs must be unique")
        return self


def load_dataset(value: Any) -> EvaluationDataset:
    if not isinstance(value, dict) or not isinstance(value.get("examples"), list):
        raise ValueError("evaluation dataset must be an object with an examples array")
    examples = []
    for example in value["examples"]:
        if not isinstance(example, dict):
            raise ValueError("each evaluation example must be an object")
        examples.append(
            {
                **example,
                "message": parse_inbox_message(example.get("message")).model_dump(mode="json"),
            }
        )
    dataset = EvaluationDataset.model_validate_json(json.dumps({**value, "examples": examples}))
    if dataset.prompt_version != PROMPT_VERSION:
        raise ValueError(
            f"dataset ground truth was recorded for prompt {dataset.prompt_version!r}; "
            f"the analyser uses {PROMPT_VERSION!r}"
        )
    return dataset


@dataclass(frozen=True)
class Observation:
    decision: str | None
    transaction: dict[str, str | int] | None
    failure: str | None
    latency_ms: float
    usage: TokenUsage | None = None


def _observe(analyser: Analyser, message: InboxMessage, clock: Callable[[], float]) -> Observation:
    started = clock()
    try:
        output = analyser.analyse(ExpenseAnalysisInput.from_inbox_message(message))
    except AnalysisError as error:
        if isinstance(error, ContextOverflowError):
            failure = "context_overflow"
        elif isinstance(error.__cause__, httpx.HTTPError):
            failure = "operational_failure"
        else:
            failure = "invalid_output"
        return Observation(None, None, failure, (clock() - started) * 1000)
    transaction = output.transaction.model_dump(mode="json") if output.transaction else None
    return Observation(
        output.decision.value,
        transaction,
        None,
        (clock() - started) * 1000,
        getattr(analyser, "last_usage", None),
    )


def _merchant_key(value: str | int) -> str:
    return " ".join(str(value).casefold().split())


def _field_matches(expected: Transaction, actual: dict[str, str | int]) -> dict[str, bool]:
    wanted = expected.model_dump(mode="json")
    matches = {name: wanted[name] == actual[name] for name in TRANSACTION_FIELDS}
    matches["merchant"] = _merchant_key(wanted["merchant"]) == _merchant_key(actual["merchant"])
    return matches


def _error_categories(expected: ExpectedOutcome, observed: Observation) -> list[str]:
    if observed.failure:
        return [observed.failure]
    wanted = expected.decision.value
    actual = observed.decision
    if actual == "expense" and wanted != "expense":
        return ["false_expense"]
    if wanted == "expense" and actual == "not_expense":
        return ["missed_expense"]
    if wanted == "expense" and actual == "needs_review":
        return ["expense_sent_to_review"]
    if wanted == "needs_review" and actual == "not_expense":
        return ["missed_review"]
    if wanted == "not_expense" and actual == "needs_review":
        return ["unnecessary_review"]
    if wanted == "expense" and expected.transaction and observed.transaction:
        matches = _field_matches(expected.transaction, observed.transaction)
        return [f"{name}_mismatch" for name in TRANSACTION_FIELDS if not matches[name]]
    return []


def _run_summary(
    examples: Sequence[EvaluationExample], observations: Sequence[Observation]
) -> dict[str, Any]:
    confusion: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    field_correct: Counter[str] = Counter()
    extracted = 0
    exact = 0
    correct_reviews = 0
    correct_decisions = 0
    for example, observed in zip(examples, observations):
        wanted = example.expected.decision.value
        confusion[f"{wanted}->{observed.decision or observed.failure}"] += 1
        categories.update(_error_categories(example.expected, observed))
        correct_decisions += observed.decision == wanted
        correct_reviews += wanted == observed.decision == "needs_review"
        if example.expected.transaction and observed.transaction:
            extracted += 1
            matches = _field_matches(example.expected.transaction, observed.transaction)
            field_correct.update(name for name, matched in matches.items() if matched)
            exact += all(matches.values())
    expected_expenses = sum(
        example.expected.decision is AnalysisDecision.EXPENSE for example in examples
    )
    return {
        "examples": len(examples),
        "correct_decisions": correct_decisions,
        "false_expenses": categories["false_expense"],
        "missed_expenses": categories["missed_expense"],
        "expenses_sent_to_review": categories["expense_sent_to_review"],
        "correct_reviews": correct_reviews,
        "unnecessary_reviews": categories["unnecessary_review"],
        "missed_reviews": categories["missed_review"],
        "invalid_outputs": categories["invalid_output"],
        "operational_failures": categories["operational_failure"],
        "context_overflows": categories["context_overflow"],
        "expected_expenses": expected_expenses,
        "extracted_expenses": extracted,
        "field_accuracy": {name: field_correct[name] for name in TRANSACTION_FIELDS},
        "exact_transactions": exact,
        "confusion": dict(sorted(confusion.items())),
    }


def _latency_summary(latencies: Sequence[float]) -> dict[str, float]:
    ordered = sorted(latencies)
    p95_index = max(0, round(0.95 * len(ordered)) - 1)
    return {
        "min": round(ordered[0], 1),
        "median": round(statistics.median(ordered), 1),
        "p95": round(ordered[p95_index], 1),
        "max": round(ordered[-1], 1),
    }


def _token_summary(usages: Sequence[TokenUsage]) -> dict[str, Any] | None:
    if not usages:
        return None
    prompt = [usage.prompt_tokens for usage in usages]
    generated = [usage.generated_tokens for usage in usages]
    return {
        "measured_calls": len(usages),
        "prompt": {"median": statistics.median(prompt), "max": max(prompt)},
        "generated": {"median": statistics.median(generated), "max": max(generated)},
    }


def _purchases_summary(examples: Sequence[EvaluationExample]) -> dict[str, Any] | None:
    if all(example.gmail_purchases is None for example in examples):
        return None
    keys = {True: "in_purchases", False: "outside_purchases", None: "unknown"}
    membership = {decision.value: dict.fromkeys(keys.values(), 0) for decision in AnalysisDecision}
    for example in examples:
        membership[example.expected.decision.value][keys[example.gmail_purchases]] += 1
    return membership


def _memory_summary(analyser: Analyser) -> dict[str, float] | None:
    read_memory = getattr(analyser, "loaded_model_memory", None)
    if read_memory is None:
        return None
    try:
        memory = read_memory()
    except AnalysisError:
        return None
    if memory is None or memory.total_bytes <= 0:
        return None
    return {
        "total_gib": round(memory.total_bytes / 2**30, 2),
        "gpu_fraction": round(memory.gpu_bytes / memory.total_bytes, 2),
    }


def evaluate(
    dataset: EvaluationDataset,
    analyser: Analyser,
    *,
    runs: int = 1,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    if runs < 1:
        raise ValueError("runs must be at least 1")
    metadata = analyser.verify_runtime()
    examples = dataset.examples
    run_observations = [
        [_observe(analyser, example.message, clock) for example in examples] for _ in range(runs)
    ]
    model_memory = _memory_summary(analyser)

    per_example = []
    decision_consistent = 0
    output_consistent = 0
    for index, example in enumerate(examples):
        observed = [observations[index] for observations in run_observations]
        decision_consistent += len({o.decision or o.failure for o in observed}) == 1
        output_consistent += (
            len({json.dumps([o.decision or o.failure, o.transaction]) for o in observed}) == 1
        )
        categories = sorted(
            {category for o in observed for category in _error_categories(example.expected, o)}
        )
        per_example.append(
            {
                "index": index,
                "message_id": example.message.source.message_id,
                "expected_decision": example.expected.decision.value,
                "gmail_purchases": example.gmail_purchases,
                "observed": [o.decision or o.failure for o in observed],
                "error_categories": categories,
            }
        )

    summary = {
        "metadata": {
            "dataset_version": dataset.dataset_version,
            **asdict(metadata),
            "runs": runs,
        },
        "runs": [_run_summary(examples, observations) for observations in run_observations],
        "consistency": {
            "examples": len(examples),
            "decision_consistent": decision_consistent,
            "output_consistent": output_consistent,
        },
        "latency_ms": _latency_summary(
            [o.latency_ms for observations in run_observations for o in observations]
        ),
        "tokens": _token_summary(
            [o.usage for observations in run_observations for o in observations if o.usage]
        ),
        "model_memory": model_memory,
        "purchases_membership": _purchases_summary(examples),
        "error_categories": dict(
            sorted(Counter(c for entry in per_example for c in entry["error_categories"]).items())
        ),
    }
    return {"summary": summary, "examples": per_example}
