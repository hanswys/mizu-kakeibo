"""One job-tracker run over a batch of messages (contract §9).

Each message is checked against the job tracker's own store before any model call, analysed,
guarded, validated, and grounded, then stored with its decision applied in one transaction.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Any

from ..constants import ANALYSIS_FAILURE_LIMIT, INBOX_MESSAGE_CONTRACT
from ..models import InboxMessage
from ..normalization import normalize_gmail_message
from ..ollama import AnalysisError, ContextOverflowError, MessageAnalysisError
from ..processor import ProcessingStatus, sanitize_reason
from ..storage import AlreadyProcessedError
from .analyser import JobAnalyser
from .constants import (
    JOB_ANALYSIS_OUTPUT_CONTRACT,
    REPEATED_FAILURE_REASON,
    SKIM_SENDER_ADDRESSES,
    SKIM_SENDER_DOMAINS,
    SKIM_SUBJECT_WORDS,
    TOO_LONG_REASON,
)
from .models import (
    JobAnalysisInput,
    JobAnalysisOutput,
    JobDecision,
    JobMissingField,
    JobStatus,
    ground,
    present_company,
    present_job_title,
)
from .names import fold_text
from .storage import AnalysisRecord, Extracted, JobStore
from .tracker import LinkEffect


def _content_review(reason: str) -> JobAnalysisOutput:
    return JobAnalysisOutput(
        contract_version=JOB_ANALYSIS_OUTPUT_CONTRACT,
        decision=JobDecision.NEEDS_REVIEW,
        reason=reason,
        application=None,
        missing_fields=[JobMissingField.MESSAGE_CONTENT],
    )


# An over-budget prompt has no trustworthy model output, so the message goes to review.
_OVERSIZED_REVIEW = _content_review(TOO_LONG_REASON)
# A message whose own analysis keeps failing goes to review instead of being retried forever.
_REPEATED_FAILURE_REVIEW = _content_review(REPEATED_FAILURE_REASON)

GROUPS = ("status_update", "not_application", "review", "skipped", "failed", "deferred")


def parse_job_message(value: Any) -> InboxMessage:
    """Accept an `inbox-message/v0` envelope, or normalize a Gmail response preferring HTML."""
    if isinstance(value, dict) and value.get("contract_version") == INBOX_MESSAGE_CONTRACT:
        return InboxMessage.model_validate_json(json.dumps(value))
    return normalize_gmail_message(value, prefer_html=True)


@dataclass(frozen=True)
class JobProcessingResult:
    provider: str
    message_id: str
    status: ProcessingStatus
    decision: str | None = None
    group: str | None = None
    effect: LinkEffect | None = None
    error: str | None = None


def _extracted(output: JobAnalysisOutput) -> Extracted:
    application = output.application
    if application is None:
        return Extracted()
    return Extracted(application.company, application.job_title, application.status)


def _raw_extracted(content: str) -> Extracted:
    """Read the names and status a replaced raw output still carries, leaving out placeholders."""
    application = json.loads(content).get("application")
    if not isinstance(application, dict):
        return Extracted()
    company, job_title, status = (
        application.get(name) for name in ("company", "job_title", "status")
    )
    return Extracted(
        present_company(company),
        present_job_title(job_title),
        JobStatus(status) if status in {item.value for item in JobStatus} else None,
    )


def _fallback_reason(output: JobAnalysisOutput) -> str:
    if output.decision is JobDecision.STATUS_UPDATE:
        return "Email reports an application status"
    if output.decision is JobDecision.NOT_APPLICATION:
        return "Email reports no application status"
    if output.missing_fields:
        return "Values need review: " + ", ".join(field.value for field in output.missing_fields)
    return "Email needs review"


class JobProcessor:
    def __init__(self, store: JobStore, analyser: JobAnalyser) -> None:
        self.store = store
        self.analyser = analyser

    def _analyse(self, analyser_input: JobAnalysisInput, message: InboxMessage) -> AnalysisRecord:
        output = self.analyser.analyse(analyser_input)
        original = getattr(self.analyser, "last_original_output", None)
        if original is not None:
            return AnalysisRecord(
                output,
                _raw_extracted(original),
                missing_value_replaced=True,
                original_output=original,
            )
        grounded = ground(analyser_input, output)
        if grounded is not output:
            return AnalysisRecord(
                grounded,
                _extracted(output),
                grounding_replaced=True,
                original_output=output.model_dump_json(),
            )
        extracted = _extracted(output)
        private_values = [message.sender, message.subject, extracted.company, extracted.job_title]
        reason = sanitize_reason(
            output.reason, [value for value in private_values if value], message.content.text
        )
        return AnalysisRecord(
            output.model_copy(update={"reason": reason or _fallback_reason(output)}), extracted
        )

    def process_one(
        self, message: InboxMessage, *, reevaluate: bool = False
    ) -> JobProcessingResult:
        provider = message.source.provider
        message_id = message.source.message_id
        processed = self.store.is_processed(provider, message_id)
        if processed and not reevaluate:
            return JobProcessingResult(provider, message_id, ProcessingStatus.SKIPPED)
        try:
            analyser_input = JobAnalysisInput.from_inbox_message(message)
            try:
                record = self._analyse(analyser_input, message)
            except ContextOverflowError:
                record = AnalysisRecord(_OVERSIZED_REVIEW)
            except MessageAnalysisError:
                # A failed re-evaluation must not replace a processed message's decision.
                if (
                    processed
                    or self.store.record_failure(provider, message_id) < ANALYSIS_FAILURE_LIMIT
                ):
                    raise
                record = AnalysisRecord(_REPEATED_FAILURE_REVIEW)
            metadata = self.analyser.verify_runtime()
            outcome = self.store.save_analysis(message, record, metadata, reevaluate=reevaluate)
        except AlreadyProcessedError:
            return JobProcessingResult(provider, message_id, ProcessingStatus.SKIPPED)
        except AnalysisError as error:
            return JobProcessingResult(
                provider, message_id, ProcessingStatus.FAILED, error=str(error)
            )
        if outcome.link is not None:
            group = "status_update"
        elif outcome.review is not None:
            group = "review"
        else:
            group = "not_application"
        return JobProcessingResult(
            provider,
            message_id,
            ProcessingStatus.PROCESSED,
            decision=record.output.decision.value,
            group=group,
            effect=outcome.link.effect if outcome.link else None,
        )

    def process_batch(
        self, messages: Iterable[InboxMessage], *, reevaluate: bool = False
    ) -> list[JobProcessingResult]:
        return [self.process_one(message, reevaluate=reevaluate) for message in messages]


def looks_job_related(message: InboxMessage) -> bool:
    """Whether a not_application message belongs on the skim list (contract §9)."""
    address = parseaddr(message.sender)[1].strip().lower()
    domain = address.rpartition("@")[2]
    if address in SKIM_SENDER_ADDRESSES or any(
        domain == known or domain.endswith(f".{known}") for known in SKIM_SENDER_DOMAINS
    ):
        return True
    return not SKIM_SUBJECT_WORDS.isdisjoint(fold_text(message.subject).split())


def run_report(
    results: Sequence[JobProcessingResult],
    messages: dict[str, InboxMessage],
    *,
    skipped: Sequence[str] = (),
    deferred: Sequence[str] = (),
) -> dict[str, Any]:
    """Report a run's message IDs in six groups, its link effects, and its skim list."""
    summary: dict[str, list[str]] = {group: [] for group in GROUPS}
    summary["skipped"].extend(skipped)
    summary["deferred"].extend(deferred)
    for result in results:
        summary[result.group or result.status.value].append(result.message_id)
    effects = Counter(result.effect for result in results if result.effect is not None)
    skim = [
        messages[result.message_id]
        for result in results
        if result.group == "not_application" and looks_job_related(messages[result.message_id])
    ]
    return {
        "summary": summary,
        "applications": {effect.value: effects[effect] for effect in LinkEffect},
        "skim": [
            {
                "message_id": message.source.message_id,
                "sender": message.sender,
                "subject": message.subject,
            }
            for message in skim
        ],
        "results": [
            {
                "provider": result.provider,
                "message_id": result.message_id,
                "status": result.status.value,
                "decision": result.decision,
                "group": result.group,
                "effect": result.effect.value if result.effect else None,
                "error": result.error,
            }
            for result in results
        ],
    }
