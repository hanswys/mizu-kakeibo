from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .constants import ANALYSIS_OUTPUT_CONTRACT
from .models import (
    AnalysisDecision,
    ExpenseAnalysisInput,
    ExpenseAnalysisOutput,
    InboxMessage,
    MissingField,
)
from .ollama import AnalysisError, ContextOverflowError, RuntimeMetadata
from .storage import AlreadyProcessedError, Store

# An over-budget prompt has no trustworthy model output, so the message goes to review.
_OVERSIZED_REVIEW = ExpenseAnalysisOutput(
    contract_version=ANALYSIS_OUTPUT_CONTRACT,
    decision=AnalysisDecision.NEEDS_REVIEW,
    reason="Message is too long for the analyser context",
    transaction=None,
    missing_fields=[MissingField.MESSAGE_CONTENT],
)


class Analyser(Protocol):
    def analyse(self, analyser_input: ExpenseAnalysisInput) -> ExpenseAnalysisOutput: ...

    def verify_runtime(self) -> RuntimeMetadata: ...


class ProcessingStatus(str, Enum):
    PROCESSED = "processed"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class ProcessingResult:
    provider: str
    message_id: str
    status: ProcessingStatus
    decision: str | None = None
    error: str | None = None


class InboxProcessor:
    def __init__(self, store: Store, analyser: Analyser) -> None:
        self.store = store
        self.analyser = analyser

    def process_one(self, message: InboxMessage, *, reevaluate: bool = False) -> ProcessingResult:
        provider = message.source.provider
        message_id = message.source.message_id
        if not reevaluate and self.store.is_processed(provider, message_id):
            return ProcessingResult(provider, message_id, ProcessingStatus.SKIPPED)
        try:
            analyser_input = ExpenseAnalysisInput.from_inbox_message(message)
            try:
                output = self.analyser.analyse(analyser_input)
                persisted_output = output.model_copy(
                    update={"reason": _sanitized_reason(output, message)}
                )
            except ContextOverflowError:
                output = persisted_output = _OVERSIZED_REVIEW
            metadata = self.analyser.verify_runtime()
            self.store.save_analysis(message, persisted_output, metadata, reevaluate=reevaluate)
            return ProcessingResult(
                provider,
                message_id,
                ProcessingStatus.PROCESSED,
                decision=output.decision.value,
            )
        except AlreadyProcessedError:
            return ProcessingResult(provider, message_id, ProcessingStatus.SKIPPED)
        except AnalysisError as error:
            return ProcessingResult(
                provider,
                message_id,
                ProcessingStatus.FAILED,
                error=str(error),
            )

    def process_batch(
        self, messages: Iterable[InboxMessage], *, reevaluate: bool = False
    ) -> list[ProcessingResult]:
        return [self.process_one(message, reevaluate=reevaluate) for message in messages]


def _fallback_reason(output: ExpenseAnalysisOutput) -> str:
    if output.decision.value == "expense":
        return "Completed purchase with all required transaction fields"
    if output.decision.value == "not_expense":
        return "Message does not establish a completed expense"
    if output.missing_fields:
        fields = ", ".join(field.value for field in output.missing_fields)
        return f"Required fields need review: {fields}"
    return "Evidence requires review under the Version 0 policy"


def _sanitized_reason(output: ExpenseAnalysisOutput, message: InboxMessage) -> str:
    reason = unicodedata.normalize("NFKC", output.reason)
    reason = "".join(
        character for character in reason if not unicodedata.category(character).startswith("C")
    )
    reason = re.sub(r"\s+", " ", reason).strip()
    reason = re.sub(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[redacted]", reason)
    reason = re.sub(r"(?i)\bhttps?://\S+", "[redacted]", reason)
    reason = re.sub(r"(?<!\d)\d(?:[ -]?\d){5,}(?!\d)", "[redacted]", reason)

    private_values = [message.sender, message.subject]
    if output.transaction is not None:
        private_values.append(output.transaction.merchant)
    for private_value in private_values:
        private_value = private_value.strip()
        if len(private_value) >= 3:
            reason = re.sub(re.escape(private_value), "[redacted]", reason, flags=re.IGNORECASE)

    evidence_words = re.findall(r"\w+", message.content.text.casefold())
    reason_words = " ".join(re.findall(r"\w+", reason.casefold()))
    for index in range(max(0, len(evidence_words) - 3)):
        phrase = " ".join(evidence_words[index : index + 4])
        if len(phrase) >= 16 and phrase in reason_words:
            return _fallback_reason(output)
    return reason or _fallback_reason(output)
