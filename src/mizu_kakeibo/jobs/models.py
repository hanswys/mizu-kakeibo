from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from ..models import AnalysisMessage, ContractModel, InboxMessage
from .constants import (
    GROUNDING_FAILURE_REASON,
    JOB_ANALYSIS_INPUT_CONTRACT,
    JOB_ANALYSIS_OUTPUT_CONTRACT,
)
from .names import company_key, contains_words, fold_text, job_title_key


class JobAnalysisInput(ContractModel):
    contract_version: Literal[JOB_ANALYSIS_INPUT_CONTRACT]
    message: AnalysisMessage

    @classmethod
    def from_inbox_message(cls, message: InboxMessage) -> JobAnalysisInput:
        return cls(
            contract_version=JOB_ANALYSIS_INPUT_CONTRACT,
            message=AnalysisMessage(
                received_at=message.received_at,
                sender=message.sender,
                subject=message.subject,
                text=message.content.text,
                truncated=message.content.truncated,
            ),
        )


class JobDecision(str, Enum):
    STATUS_UPDATE = "status_update"
    NOT_APPLICATION = "not_application"
    NEEDS_REVIEW = "needs_review"


class JobStatus(str, Enum):
    APPLIED = "applied"
    VIEWED = "viewed"
    INTERVIEW = "interview"
    REJECTED = "rejected"
    OFFERED = "offered"


class JobMissingField(str, Enum):
    COMPANY = "company"
    JOB_TITLE = "job_title"
    STATUS = "status"
    MESSAGE_CONTENT = "message_content"


class JobApplication(ContractModel):
    company: str = Field(max_length=200)
    job_title: str = Field(max_length=200)
    status: JobStatus

    @field_validator("company")
    @classmethod
    def company_must_have_a_key(cls, value: str) -> str:
        if not value.strip() or not company_key(value):
            raise ValueError("company must have a non-empty normalized key")
        return value

    @field_validator("job_title")
    @classmethod
    def job_title_must_have_a_key(cls, value: str) -> str:
        if not value.strip() or not job_title_key(value):
            raise ValueError("job_title must have a non-empty normalized key")
        return value


class JobAnalysisOutput(ContractModel):
    contract_version: Literal[JOB_ANALYSIS_OUTPUT_CONTRACT]
    decision: JobDecision
    reason: str = Field(max_length=200)
    application: JobApplication | None
    missing_fields: list[JobMissingField]

    @field_validator("missing_fields")
    @classmethod
    def missing_fields_must_be_unique(cls, value: list[JobMissingField]) -> list[JobMissingField]:
        if len(value) != len(set(value)):
            raise ValueError("missing_fields must not contain duplicates")
        return value

    @model_validator(mode="after")
    def enforce_decision_invariants(self) -> JobAnalysisOutput:
        if (self.decision is JobDecision.STATUS_UPDATE) != (self.application is not None):
            raise ValueError("application is required for status_update and only for it")
        if self.decision is not JobDecision.NEEDS_REVIEW and self.missing_fields:
            raise ValueError("only needs_review may list missing fields")
        return self


def ground(analyser_input: JobAnalysisInput, output: JobAnalysisOutput) -> JobAnalysisOutput:
    """Send a status update to review when its company or job title is not in the message.

    Each name must occur, folded, as a whole-word sequence in the folded subject or body.
    The caller keeps the original output; this returns the decision to act on.
    """
    application = output.application
    if application is None:
        return output
    subject = fold_text(analyser_input.message.subject)
    body = fold_text(analyser_input.message.text)
    missing = [
        field
        for field, value in (
            (JobMissingField.COMPANY, application.company),
            (JobMissingField.JOB_TITLE, application.job_title),
        )
        if not any(contains_words(text, fold_text(value)) for text in (subject, body))
    ]
    if not missing:
        return output
    return JobAnalysisOutput(
        contract_version=JOB_ANALYSIS_OUTPUT_CONTRACT,
        decision=JobDecision.NEEDS_REVIEW,
        reason=GROUNDING_FAILURE_REASON,
        application=None,
        missing_fields=missing,
    )
