from __future__ import annotations

import json
from collections.abc import Callable
from enum import Enum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from ..models import AnalysisMessage, ContractModel, InboxMessage
from .constants import (
    GROUNDING_FAILURE_REASON,
    JOB_ANALYSIS_INPUT_CONTRACT,
    JOB_ANALYSIS_OUTPUT_CONTRACT,
    MISSING_VALUE_REASON,
    PLACEHOLDER_VALUES,
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


def _is_placeholder(value: Any, key: Callable[[str], str]) -> bool:
    return (
        not isinstance(value, str)
        or not key(value)
        or value.strip().casefold() in PLACEHOLDER_VALUES
    )


def present_company(value: Any) -> str | None:
    """The company, or None when the missing-value guard treats it as missing."""
    return None if _is_placeholder(value, company_key) else value


def present_job_title(value: Any) -> str | None:
    """The job title, or None when the missing-value guard treats it as missing."""
    return None if _is_placeholder(value, job_title_key) else value


def review_missing_values(content: str) -> JobAnalysisOutput | None:
    """Send an output to review when it reports a missing value without choosing needs_review.

    This runs on the raw output before validation, because such an output violates the
    invariants. It applies to a status_update or not_application that lists missing fields, and
    to a status_update whose company or job title is empty or a placeholder. It returns None when
    it does not apply; the result still passes every other invariant or raises.
    """
    value = json.loads(content)
    if not isinstance(value, dict):
        return None
    decision = value.get("decision")
    listed = value.get("missing_fields")
    if decision not in (JobDecision.STATUS_UPDATE, JobDecision.NOT_APPLICATION) or not isinstance(
        listed, list
    ):
        return None
    missing = list(listed)
    application = value.get("application")
    if decision == JobDecision.STATUS_UPDATE and isinstance(application, dict):
        for field, key in (
            (JobMissingField.COMPANY, company_key),
            (JobMissingField.JOB_TITLE, job_title_key),
        ):
            if _is_placeholder(application.get(field.value), key) and field.value not in missing:
                missing.append(field.value)
    if not missing:
        return None
    return JobAnalysisOutput.model_validate_json(
        json.dumps(
            {
                **value,
                "decision": JobDecision.NEEDS_REVIEW.value,
                "reason": MISSING_VALUE_REASON,
                "application": None,
                "missing_fields": missing,
            }
        )
    )
