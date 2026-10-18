"""The Version 0 job-tracker evaluation (contract §11).

Every example is normalized preferring HTML, analysed, validated, and grounded, then the results
are replayed in received order on an empty in-memory store. Nothing is written to disk.
"""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import httpx
from pydantic import Field, field_validator, model_validator

from ..constants import INBOX_MESSAGE_CONTRACT
from ..evaluation import _latency_summary, _memory_summary, _token_summary
from ..models import ContractModel, InboxMessage
from ..normalization import normalize_gmail_message
from ..ollama import AnalysisError, ContextOverflowError, TokenUsage
from .analyser import JobAnalyser
from .constants import (
    JOB_ANALYSIS_INPUT_CONTRACT,
    JOB_ANALYSIS_OUTPUT_CONTRACT,
    JOBS_CONTRACT_VERSION,
)
from .models import (
    JobAnalysisInput,
    JobAnalysisOutput,
    JobApplication,
    JobDecision,
    JobMissingField,
    JobStatus,
    ground,
)
from .names import company_key, job_title_key
from .tracker import (
    InMemoryStore,
    Outcome,
    ReviewCause,
    StatusUpdate,
    apply_decision,
    replay_status,
)

APPLICATION_FIELDS = ("company", "job_title", "status")


class ExpectedOutcome(ContractModel):
    decision: JobDecision
    application: JobApplication | None
    application_key: str | None = Field(min_length=1)
    missing_fields: list[JobMissingField] | None = None

    @field_validator("missing_fields")
    @classmethod
    def missing_fields_are_labelled_values(
        cls, value: list[JobMissingField] | None
    ) -> list[JobMissingField] | None:
        if value is None:
            return value
        if JobMissingField.MESSAGE_CONTENT in value:
            raise ValueError("expected missing_fields may list only company, job_title, and status")
        if len(value) != len(set(value)):
            raise ValueError("expected missing_fields must not contain duplicates")
        return value

    @model_validator(mode="after")
    def application_only_for_status_update(self) -> ExpectedOutcome:
        is_update = self.decision is JobDecision.STATUS_UPDATE
        if is_update != (self.application is not None) or is_update != (
            self.application_key is not None
        ):
            raise ValueError(
                "expected application and application_key are required for status_update "
                "and only for it"
            )
        if self.decision is not JobDecision.NEEDS_REVIEW and self.missing_fields:
            raise ValueError("only an expected needs_review may list missing fields")
        return self


class EvaluationExample(ContractModel):
    message: InboxMessage
    synthetic: bool
    expected: ExpectedOutcome


class ExpectedApplication(ContractModel):
    key: str = Field(min_length=1)
    company: str = Field(min_length=1)
    job_title: str = Field(min_length=1)
    status: JobStatus
    synthetic: bool


class EvaluationDataset(ContractModel):
    dataset_version: str = Field(min_length=1)
    contract_version: str
    examples: list[EvaluationExample] = Field(min_length=1)
    expected_applications: list[ExpectedApplication]

    @model_validator(mode="after")
    def labels_must_be_consistent(self) -> EvaluationDataset:
        ids = [example.message.source.message_id for example in self.examples]
        if len(ids) != len(set(ids)):
            raise ValueError("evaluation message IDs must be unique")
        applications = {application.key: application for application in self.expected_applications}
        if len(applications) != len(self.expected_applications):
            raise ValueError("expected application keys must be unique")
        updates: dict[str, list[StatusUpdate]] = defaultdict(list)
        for example in self.examples:
            key, values = example.expected.application_key, example.expected.application
            if key is None or values is None:
                continue
            application = applications.get(key)
            if application is None:
                raise ValueError(f"example key {key!r} names no expected application")
            if application.synthetic != example.synthetic:
                raise ValueError(
                    f"expected application {key!r} mixes real and synthetic examples; "
                    "a synthetic example must use a synthetic key no real example uses"
                )
            updates[key].append(
                StatusUpdate(
                    example.message.source.message_id,
                    example.message.received_at,
                    values.status,
                )
            )
        for key, application in applications.items():
            if key not in updates:
                raise ValueError(f"expected application {key!r} has no labelled status update")
            replayed = replay_status(updates[key])
            if replayed is not application.status:
                raise ValueError(
                    f"expected application {key!r} is labelled {application.status.value}, "
                    f"but its labelled updates replay to {replayed.value if replayed else None}"
                )
        return self


def _normalized_message(index: int, example: dict[str, Any]) -> dict[str, Any]:
    message = example.get("message")
    if isinstance(message, dict) and message.get("contract_version") == INBOX_MESSAGE_CONTRACT:
        if example.get("synthetic") is not True:
            raise ValueError(
                f"example {index} is real and must store the connector RAW response, "
                "not an envelope"
            )
        return message
    return normalize_gmail_message(message, prefer_html=True).model_dump(mode="json")


def load_dataset(value: Any) -> EvaluationDataset:
    if not isinstance(value, dict) or not isinstance(value.get("examples"), list):
        raise ValueError("evaluation dataset must be an object with an examples array")
    if value.get("contract_version") != JOBS_CONTRACT_VERSION:
        raise ValueError(
            f"dataset labels follow contract {value.get('contract_version')!r}; "
            f"the evaluator uses {JOBS_CONTRACT_VERSION!r}"
        )
    examples = []
    for index, example in enumerate(value["examples"]):
        if not isinstance(example, dict):
            raise ValueError("each evaluation example must be an object")
        examples.append({**example, "message": _normalized_message(index, example)})
    return EvaluationDataset.model_validate_json(json.dumps({**value, "examples": examples}))


@dataclass(frozen=True)
class Observation:
    output: JobAnalysisOutput | None
    grounding_replaced: bool
    failure: str | None
    latency_ms: float
    usage: TokenUsage | None = None

    @property
    def decision(self) -> str | None:
        return self.output.decision.value if self.output else None

    @property
    def application(self) -> JobApplication | None:
        return self.output.application if self.output else None

    def label(self) -> str:
        return self.decision or self.failure or ""

    def fingerprint(self) -> str:
        application = self.application.model_dump(mode="json") if self.application else None
        return json.dumps([self.label(), application])


def _failure_category(error: AnalysisError) -> str:
    if isinstance(error, ContextOverflowError):
        return "context_overflow"
    if isinstance(error.__cause__, httpx.HTTPError):
        return "operational_failure"
    return "invalid_output"


def _observe(
    analyser: JobAnalyser, message: InboxMessage, clock: Callable[[], float]
) -> Observation:
    analyser_input = JobAnalysisInput.from_inbox_message(message)
    started = clock()
    try:
        output = analyser.analyse(analyser_input)
    except AnalysisError as error:
        return Observation(None, False, _failure_category(error), (clock() - started) * 1000)
    latency_ms = (clock() - started) * 1000
    grounded = ground(analyser_input, output)
    return Observation(
        grounded,
        grounded is not output,
        None,
        latency_ms,
        getattr(analyser, "last_usage", None),
    )


def _field_matches(expected: JobApplication, actual: JobApplication) -> dict[str, bool]:
    return {
        "company": company_key(expected.company) == company_key(actual.company),
        "job_title": job_title_key(expected.job_title) == job_title_key(actual.job_title),
        "status": expected.status is actual.status,
    }


def _error_categories(expected: ExpectedOutcome, observed: Observation) -> list[str]:
    if observed.failure:
        return [observed.failure]
    categories = ["grounding_replaced"] if observed.grounding_replaced else []
    wanted = expected.decision.value
    actual = observed.decision
    if actual == "status_update" and wanted != "status_update":
        categories.append("false_status_update")
    elif wanted == "status_update" and actual == "not_application":
        categories.append("missed_status_update")
    elif wanted == "status_update" and actual == "needs_review":
        categories.append("status_update_sent_to_review")
    elif wanted == "needs_review" and actual == "not_application":
        categories.append("missed_review")
    elif wanted == "not_application" and actual == "needs_review":
        categories.append("unnecessary_review")
    elif expected.application and observed.application:
        matches = _field_matches(expected.application, observed.application)
        categories.extend(f"{name}_mismatch" for name in APPLICATION_FIELDS if not matches[name])
    return categories


def _replay(
    examples: Sequence[EvaluationExample], observations: Sequence[Observation]
) -> tuple[InMemoryStore, dict[str, Outcome]]:
    """Replay validated results in received order, then by message ID, resolving no review."""
    store = InMemoryStore()
    outcomes = {}
    ordered = sorted(
        zip(examples, observations),
        key=lambda pair: (pair[0].message.received_at, pair[0].message.source.message_id),
    )
    for example, observed in ordered:
        if observed.output is None:
            continue
        message = example.message
        outcomes[message.source.message_id] = apply_decision(
            store, message.source.message_id, message.received_at, observed.output
        )
    return store, outcomes


def _observed_applications(
    examples: Sequence[EvaluationExample], store: InMemoryStore
) -> list[dict[str, Any]]:
    expected_keys = {
        example.message.source.message_id: example.expected.application_key
        for example in examples
        if example.expected.application_key is not None
    }
    applications = []
    for application in store.applications():
        message_ids = [link.update.message_id for link in store.links(application.id)]
        status = store.status(application.id)
        applications.append(
            {
                "id": application.id,
                "status": status.value if status else None,
                "message_ids": message_ids,
                "expected_keys": sorted(
                    {expected_keys[mid] for mid in message_ids if mid in expected_keys}
                ),
            }
        )
    return applications


def _application_summary(
    examples: Sequence[EvaluationExample],
    expected: Sequence[ExpectedApplication],
    store: InMemoryStore,
) -> dict[str, Any]:
    observed = _observed_applications(examples, store)
    observed_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for application in observed:
        for key in application["expected_keys"]:
            observed_by_key[key].append(application)
    correct = wrong_status = 0
    for application in expected:
        matches = observed_by_key[application.key]
        if len(matches) == 1 and matches[0]["expected_keys"] == [application.key]:
            if matches[0]["status"] == application.status.value:
                correct += 1
            else:
                wrong_status += 1
    linked = {mid for application in observed for mid in application["message_ids"]}
    causes = Counter(item.cause.value for item in store.review_items)
    return {
        "expected_applications": len(expected),
        "observed_applications": len(observed),
        "correct": correct,
        "wrong_status": wrong_status,
        "merged": sum(len(application["expected_keys"]) >= 2 for application in observed),
        "extra": sum(not application["expected_keys"] for application in observed),
        "split": sum(len(observed_by_key[application.key]) >= 2 for application in expected),
        "missing": sum(not observed_by_key[application.key] for application in expected),
        "unlinked_status_updates": sum(
            example.expected.decision is JobDecision.STATUS_UPDATE
            and example.message.source.message_id not in linked
            for example in examples
        ),
        "review_items": {cause.value: causes[cause.value] for cause in ReviewCause},
    }


def _run_summary(
    examples: Sequence[EvaluationExample],
    expected_applications: Sequence[ExpectedApplication],
    observations: Sequence[Observation],
    store: InMemoryStore,
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
        confusion[f"{wanted}->{observed.label()}"] += 1
        categories.update(_error_categories(example.expected, observed))
        correct_decisions += observed.decision == wanted
        correct_reviews += wanted == observed.decision == "needs_review"
        if example.expected.application and observed.application:
            extracted += 1
            matches = _field_matches(example.expected.application, observed.application)
            field_correct.update(name for name, matched in matches.items() if matched)
            exact += all(matches.values())
    expected_updates = sum(
        example.expected.decision is JobDecision.STATUS_UPDATE for example in examples
    )
    return {
        "examples": len(examples),
        "correct_decisions": correct_decisions,
        "false_status_updates": categories["false_status_update"],
        "missed_status_updates": categories["missed_status_update"],
        "status_updates_sent_to_review": categories["status_update_sent_to_review"],
        "correct_reviews": correct_reviews,
        "unnecessary_reviews": categories["unnecessary_review"],
        "missed_reviews": categories["missed_review"],
        "grounding_replacements": categories["grounding_replaced"],
        "invalid_outputs": categories["invalid_output"],
        "operational_failures": categories["operational_failure"],
        "context_overflows": categories["context_overflow"],
        "expected_status_updates": expected_updates,
        "extracted_status_updates": extracted,
        "field_accuracy": {name: field_correct[name] for name in APPLICATION_FIELDS},
        "exact_applications": exact,
        "confusion": dict(sorted(confusion.items())),
        "applications": _application_summary(examples, expected_applications, store),
    }


def _application_list(store: InMemoryStore) -> list[Any]:
    return sorted(
        [
            *application.pair,
            getattr(store.status(application.id), "value", None),
            sorted(link.update.message_id for link in store.links(application.id)),
        ]
        for application in store.applications()
    )


@dataclass(frozen=True)
class _Group:
    indexes: list[int]
    examples: list[EvaluationExample]
    expected_applications: list[ExpectedApplication]


def _groups(dataset: EvaluationDataset) -> dict[str, _Group]:
    groups = {}
    for name, synthetic in (("real", False), ("synthetic", True)):
        indexes = [
            index
            for index, example in enumerate(dataset.examples)
            if example.synthetic is synthetic
        ]
        groups[name] = _Group(
            indexes,
            [dataset.examples[index] for index in indexes],
            [
                application
                for application in dataset.expected_applications
                if application.synthetic is synthetic
            ],
        )
    return groups


def evaluate(
    dataset: EvaluationDataset,
    analyser: JobAnalyser,
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

    groups = _groups(dataset)
    run_summaries: list[dict[str, Any]] = []
    run_applications: list[dict[str, Any]] = []
    run_outcomes: list[dict[str, Outcome]] = []
    lists: dict[str, list[list[Any]]] = defaultdict(list)
    for observations in run_observations:
        summaries: dict[str, Any] = {}
        applications: dict[str, Any] = {}
        outcomes: dict[str, Outcome] = {}
        for name, group in groups.items():
            if not group.examples:
                summaries[name] = applications[name] = None
                continue
            group_observations = [observations[index] for index in group.indexes]
            store, group_outcomes = _replay(group.examples, group_observations)
            outcomes.update(group_outcomes)
            summaries[name] = _run_summary(
                group.examples, group.expected_applications, group_observations, store
            )
            applications[name] = _observed_applications(group.examples, store)
            lists[name].append(_application_list(store))
        run_summaries.append(summaries)
        run_applications.append(applications)
        run_outcomes.append(outcomes)

    per_example = []
    consistent: dict[str, Counter[str]] = defaultdict(Counter)
    for index, example in enumerate(examples):
        message_id = example.message.source.message_id
        observed = [observations[index] for observations in run_observations]
        group = "synthetic" if example.synthetic else "real"
        consistent[group]["decision"] += len({o.label() for o in observed}) == 1
        consistent[group]["output"] += len({o.fingerprint() for o in observed}) == 1
        categories = sorted(
            {category for o in observed for category in _error_categories(example.expected, o)}
        )
        outcomes = [run.get(message_id) for run in run_outcomes]
        per_example.append(
            {
                "index": index,
                "message_id": message_id,
                "synthetic": example.synthetic,
                "expected_decision": example.expected.decision.value,
                "expected_application_key": example.expected.application_key,
                "observed": [o.label() for o in observed],
                "grounding_replaced": [o.grounding_replaced for o in observed],
                "replay": [_outcome_label(outcome) for outcome in outcomes],
                "application_ids": [
                    outcome.link.application_id if outcome and outcome.link else None
                    for outcome in outcomes
                ],
                "error_categories": categories,
            }
        )

    summary = {
        "metadata": {
            "dataset_version": dataset.dataset_version,
            "contract_version": JOBS_CONTRACT_VERSION,
            "inbox_message_contract": INBOX_MESSAGE_CONTRACT,
            "analysis_input_contract": JOB_ANALYSIS_INPUT_CONTRACT,
            "analysis_output_contract": JOB_ANALYSIS_OUTPUT_CONTRACT,
            **asdict(metadata),
            "runs": runs,
        },
        "runs": run_summaries,
        "consistency": {
            name: {
                "examples": len(group.examples),
                "decision_consistent": consistent[name]["decision"],
                "output_consistent": consistent[name]["output"],
                "application_lists_identical": all(
                    application_list == lists[name][0] for application_list in lists[name]
                ),
            }
            if group.examples
            else None
            for name, group in groups.items()
        },
        "latency_ms": _latency_summary(
            [o.latency_ms for observations in run_observations for o in observations]
        ),
        "tokens": _token_summary(
            [o.usage for observations in run_observations for o in observations if o.usage]
        ),
        "model_memory": model_memory,
        "error_categories": dict(
            sorted(Counter(c for entry in per_example for c in entry["error_categories"]).items())
        ),
    }
    return {"summary": summary, "examples": per_example, "applications": run_applications}


def _outcome_label(outcome: Outcome | None) -> str | None:
    if outcome is None:
        return None
    if outcome.link is not None:
        return outcome.link.effect.value
    if outcome.review is not None:
        return f"review:{outcome.review.cause.value}"
    return "none"
