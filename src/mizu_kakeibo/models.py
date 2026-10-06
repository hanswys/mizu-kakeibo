from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal

from iso4217 import Currency
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .constants import (
    ANALYSIS_INPUT_CONTRACT,
    ANALYSIS_OUTPUT_CONTRACT,
    INBOX_MESSAGE_CONTRACT,
)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class MessageSource(ContractModel):
    provider: Literal["gmail"]
    message_id: str = Field(min_length=1)
    thread_id: str | None


class MessageContent(ContractModel):
    text: str
    source_type: Literal["text_plain", "html_converted", "mixed_text"]
    truncated: bool


class InboxMessage(ContractModel):
    contract_version: Literal[INBOX_MESSAGE_CONTRACT]
    source: MessageSource
    received_at: datetime
    sender: str = Field(max_length=512)
    subject: str = Field(max_length=1000)
    content: MessageContent

    @field_validator("received_at")
    @classmethod
    def received_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("received_at must include a UTC offset")
        return value


class AnalysisMessage(ContractModel):
    received_at: datetime
    sender: str = Field(max_length=512)
    subject: str = Field(max_length=1000)
    text: str
    truncated: bool

    @field_validator("received_at")
    @classmethod
    def received_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("received_at must include a UTC offset")
        return value


class ExpenseAnalysisInput(ContractModel):
    contract_version: Literal[ANALYSIS_INPUT_CONTRACT]
    message: AnalysisMessage

    @classmethod
    def from_inbox_message(cls, message: InboxMessage) -> ExpenseAnalysisInput:
        return cls(
            contract_version=ANALYSIS_INPUT_CONTRACT,
            message=AnalysisMessage(
                received_at=message.received_at,
                sender=message.sender,
                subject=message.subject,
                text=message.content.text,
                truncated=message.content.truncated,
            ),
        )


class AnalysisDecision(str, Enum):
    EXPENSE = "expense"
    NOT_EXPENSE = "not_expense"
    NEEDS_REVIEW = "needs_review"


class MissingField(str, Enum):
    MERCHANT = "merchant"
    AMOUNT_MINOR = "amount_minor"
    CURRENCY = "currency"
    TRANSACTION_DATE = "transaction_date"
    MESSAGE_CONTENT = "message_content"


class Transaction(ContractModel):
    merchant: str = Field(min_length=1)
    amount_minor: int = Field(gt=0)
    currency: str
    transaction_date: date

    @field_validator("merchant")
    @classmethod
    def merchant_must_not_be_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("merchant must not be blank")
        return value

    @field_validator("currency")
    @classmethod
    def currency_must_be_iso_4217(cls, value: str) -> str:
        if len(value) != 3 or not value.isascii() or not value.isupper():
            raise ValueError("currency must be an uppercase ISO 4217 code")
        try:
            Currency(value)
        except ValueError as error:
            raise ValueError("currency must be an uppercase ISO 4217 code") from error
        return value


class ExpenseAnalysisOutput(ContractModel):
    contract_version: Literal[ANALYSIS_OUTPUT_CONTRACT]
    decision: AnalysisDecision
    reason: str = Field(min_length=1, max_length=500)
    transaction: Transaction | None
    missing_fields: list[MissingField]

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must not be blank")
        return value

    @field_validator("missing_fields")
    @classmethod
    def missing_fields_must_be_unique(cls, value: list[MissingField]) -> list[MissingField]:
        if len(value) != len(set(value)):
            raise ValueError("missing_fields must not contain duplicates")
        return value

    @model_validator(mode="after")
    def enforce_decision_invariants(self) -> ExpenseAnalysisOutput:
        if self.decision is AnalysisDecision.EXPENSE:
            if self.transaction is None:
                raise ValueError("expense requires a transaction")
            if self.missing_fields:
                raise ValueError("expense cannot have missing fields")
        else:
            if self.transaction is not None:
                raise ValueError("transaction is only valid for expense")
            if self.decision is AnalysisDecision.NOT_EXPENSE and self.missing_fields:
                raise ValueError("not_expense cannot have missing fields")
        return self
