from datetime import date, datetime

import pytest
from pydantic import ValidationError

from mizu_kakeibo.constants import ANALYSIS_OUTPUT_CONTRACT
from mizu_kakeibo.models import (
    AnalysisDecision,
    ExpenseAnalysisInput,
    ExpenseAnalysisOutput,
    MissingField,
    Transaction,
)


def transaction() -> Transaction:
    return Transaction(
        merchant="Example Store",
        amount_minor=4250,
        currency="MYR",
        transaction_date=date(2026, 10, 6),
    )


def output(**changes: object) -> ExpenseAnalysisOutput:
    values = {
        "contract_version": ANALYSIS_OUTPUT_CONTRACT,
        "decision": AnalysisDecision.EXPENSE,
        "reason": "Completed purchase with required fields",
        "transaction": transaction(),
        "missing_fields": [],
    }
    values.update(changes)
    return ExpenseAnalysisOutput(**values)


def test_analyser_input_excludes_provider_identifiers(inbox_message) -> None:
    result = ExpenseAnalysisInput.from_inbox_message(inbox_message).model_dump(mode="json")

    assert result["contract_version"] == "expense-analysis-input/v0"
    assert "source" not in result["message"]
    assert "message_id" not in result["message"]


@pytest.mark.parametrize(
    ("decision", "transaction_value", "missing_fields"),
    [
        (AnalysisDecision.EXPENSE, None, []),
        (AnalysisDecision.EXPENSE, transaction(), [MissingField.MERCHANT]),
        (AnalysisDecision.NOT_EXPENSE, transaction(), []),
        (AnalysisDecision.NOT_EXPENSE, None, [MissingField.MESSAGE_CONTENT]),
        (AnalysisDecision.NEEDS_REVIEW, transaction(), []),
    ],
)
def test_output_rejects_decision_invariant_violations(
    decision, transaction_value, missing_fields
) -> None:
    with pytest.raises(ValidationError):
        output(
            decision=decision,
            transaction=transaction_value,
            missing_fields=missing_fields,
        )


def test_needs_review_accepts_supported_missing_fields() -> None:
    result = output(
        decision=AnalysisDecision.NEEDS_REVIEW,
        transaction=None,
        missing_fields=[MissingField.CURRENCY, MissingField.TRANSACTION_DATE],
    )
    assert result.missing_fields == [MissingField.CURRENCY, MissingField.TRANSACTION_DATE]


@pytest.mark.parametrize("currency", ["myr", "ZZZ", "US", "USDD"])
def test_transaction_rejects_invalid_iso_currency(currency: str) -> None:
    with pytest.raises(ValidationError):
        Transaction(
            merchant="Store",
            amount_minor=1,
            currency=currency,
            transaction_date=date(2026, 10, 6),
        )


def test_transaction_rejects_float_minor_units() -> None:
    with pytest.raises(ValidationError):
        Transaction(
            merchant="Store",
            amount_minor=42.5,
            currency="MYR",
            transaction_date=date(2026, 10, 6),
        )


def test_contract_models_reject_unknown_fields(inbox_message) -> None:
    value = inbox_message.model_dump(mode="json")
    value["unknown"] = True
    with pytest.raises(ValidationError):
        type(inbox_message).model_validate(value)


def test_received_at_requires_timezone(inbox_message) -> None:
    value = inbox_message.model_dump()
    value["received_at"] = datetime(2026, 10, 6, 1, 30)
    with pytest.raises(ValidationError):
        type(inbox_message).model_validate(value)
