from __future__ import annotations

import csv
import os
import sqlite3
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from mizu_kakeibo.constants import ANALYSIS_OUTPUT_CONTRACT
from mizu_kakeibo.models import (
    AnalysisDecision,
    ExpenseAnalysisOutput,
    MissingField,
    Transaction,
)
from mizu_kakeibo.ollama import AnalysisError, ContextOverflowError, RuntimeMetadata
from mizu_kakeibo.processor import InboxProcessor, ProcessingStatus
from mizu_kakeibo.storage import ReviewResolutionError, Store

METADATA = RuntimeMetadata(
    runtime="ollama",
    runtime_version="0.23.1",
    model="qwen2.5:14b",
    model_digest="sha256-test",
    prompt_version="expense-v0.1",
    temperature=0,
    seed=42,
    context_size=32768,
    max_generated_tokens=512,
)


def expense_output(amount=4250):
    return ExpenseAnalysisOutput(
        contract_version=ANALYSIS_OUTPUT_CONTRACT,
        decision=AnalysisDecision.EXPENSE,
        reason="Completed purchase with required fields",
        transaction=Transaction(
            merchant="Example Store",
            amount_minor=amount,
            currency="MYR",
            transaction_date=date(2026, 10, 6),
        ),
        missing_fields=[],
    )


def review_output():
    return ExpenseAnalysisOutput(
        contract_version=ANALYSIS_OUTPUT_CONTRACT,
        decision=AnalysisDecision.NEEDS_REVIEW,
        reason="Required transaction details are incomplete",
        transaction=None,
        missing_fields=[MissingField.TRANSACTION_DATE],
    )


class FakeAnalyser:
    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error
        self.calls = 0

    def analyse(self, analyser_input):
        self.calls += 1
        if self.error:
            raise self.error
        return self.output

    def verify_runtime(self):
        return METADATA


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "private.sqlite3", tmp_path / "ledger.csv")


def read_ledger(store):
    with store.ledger_path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def test_expense_persists_decision_and_one_ledger_row(store, inbox_message) -> None:
    analyser = FakeAnalyser(expense_output())
    result = InboxProcessor(store, analyser).process_one(inbox_message)

    assert result.status is ProcessingStatus.PROCESSED
    assert result.decision == "expense"
    assert read_ledger(store) == [
        {
            "source_provider": "gmail",
            "gmail_message_id": "message-1",
            "merchant": "Example Store",
            "amount_minor": "4250",
            "currency": "MYR",
            "transaction_date": "2026-10-06",
        }
    ]
    with sqlite3.connect(store.database_path) as connection:
        analysis = connection.execute(
            "SELECT decision, inbox_contract_version, analyser_input_contract_version, "
            "analyser_output_contract_version, prompt_version FROM analyses"
        ).fetchone()
        assert analysis == (
            "expense",
            "inbox-message/v0",
            "expense-analysis-input/v0",
            "expense-analysis-output/v0",
            "expense-v0.1",
        )


def test_replay_skips_before_model_call(store, inbox_message) -> None:
    first = FakeAnalyser(expense_output())
    InboxProcessor(store, first).process_one(inbox_message)
    replay = FakeAnalyser(expense_output())

    result = InboxProcessor(store, replay).process_one(inbox_message)
    assert result.status is ProcessingStatus.SKIPPED
    assert replay.calls == 0
    assert len(read_ledger(store)) == 1


def test_filters_processed_ids_before_retrieval(store, inbox_message) -> None:
    InboxProcessor(store, FakeAnalyser(expense_output())).process_one(inbox_message)
    assert store.filter_new_ids("gmail", ["message-1", "message-2", "message-2"]) == ["message-2"]


def test_overlapping_runs_persist_only_one_active_decision(store, inbox_message) -> None:
    barrier = threading.Barrier(2)

    class OverlappingAnalyser(FakeAnalyser):
        def analyse(self, analyser_input):
            result = super().analyse(analyser_input)
            barrier.wait(timeout=5)
            return result

    analysers = [OverlappingAnalyser(expense_output()), OverlappingAnalyser(expense_output())]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda analyser: InboxProcessor(store, analyser).process_one(inbox_message),
                analysers,
            )
        )

    assert sorted(result.status.value for result in results) == ["processed", "skipped"]
    assert len(read_ledger(store)) == 1
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM analyses").fetchone()[0] == 1


def test_explicit_reevaluation_replaces_active_transaction(store, inbox_message) -> None:
    InboxProcessor(store, FakeAnalyser(expense_output(4250))).process_one(inbox_message)
    result = InboxProcessor(store, FakeAnalyser(expense_output(9900))).process_one(
        inbox_message, reevaluate=True
    )

    assert result.status is ProcessingStatus.PROCESSED
    assert [row["amount_minor"] for row in read_ledger(store)] == ["9900"]
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM analyses").fetchone()[0] == 2
        active_count = connection.execute(
            "SELECT COUNT(*) FROM analyses WHERE active = 1"
        ).fetchone()[0]
        assert active_count == 1


def test_review_is_exposed_then_corrected_without_changing_original(store, inbox_message) -> None:
    InboxProcessor(store, FakeAnalyser(review_output())).process_one(inbox_message)
    items = store.list_review_items()
    assert len(items) == 1
    assert items[0].missing_fields == ["transaction_date"]
    assert items[0].reason == "Required transaction details are incomplete"
    assert read_ledger(store) == []

    corrected = Transaction(
        merchant="Corrected Store",
        amount_minor=1234,
        currency="MYR",
        transaction_date=date(2026, 10, 5),
    )
    store.resolve_review("gmail", "message-1", AnalysisDecision.EXPENSE, corrected)

    assert store.list_review_items() == []
    assert read_ledger(store)[0]["merchant"] == "Corrected Store"
    with sqlite3.connect(store.database_path) as connection:
        original, resolution = connection.execute(
            "SELECT a.decision, r.decision FROM analyses a "
            "JOIN resolutions r ON r.analysis_id = a.id"
        ).fetchone()
        assert original == "needs_review"
        assert resolution == "expense"


def test_review_can_be_rejected_without_a_transaction(store, inbox_message) -> None:
    InboxProcessor(store, FakeAnalyser(review_output())).process_one(inbox_message)
    store.resolve_review("gmail", "message-1", AnalysisDecision.NOT_EXPENSE)
    assert store.list_review_items() == []
    assert read_ledger(store) == []


def test_non_review_cannot_be_resolved(store, inbox_message) -> None:
    InboxProcessor(store, FakeAnalyser(expense_output())).process_one(inbox_message)
    with pytest.raises(ReviewResolutionError):
        store.resolve_review("gmail", "message-1", AnalysisDecision.NOT_EXPENSE)


def test_model_failure_remains_retryable(store, inbox_message) -> None:
    failed = InboxProcessor(store, FakeAnalyser(error=AnalysisError("timeout"))).process_one(
        inbox_message
    )
    assert failed.status is ProcessingStatus.FAILED
    assert store.is_processed("gmail", "message-1") is False

    retry = InboxProcessor(store, FakeAnalyser(expense_output())).process_one(inbox_message)
    assert retry.status is ProcessingStatus.PROCESSED


def test_context_overflow_goes_to_review(store, inbox_message) -> None:
    overflow = ContextOverflowError("prompt exceeded the analyser context budget")
    result = InboxProcessor(store, FakeAnalyser(error=overflow)).process_one(inbox_message)

    assert result.status is ProcessingStatus.PROCESSED
    assert result.decision == "needs_review"
    assert store.is_processed("gmail", "message-1") is True
    items = store.list_review_items()
    assert len(items) == 1
    assert items[0].missing_fields == ["message_content"]
    assert items[0].reason == "Message is too long for the analyser context"
    assert read_ledger(store) == []


def test_not_expense_creates_no_transaction_or_review(store, inbox_message) -> None:
    output = ExpenseAnalysisOutput(
        contract_version=ANALYSIS_OUTPUT_CONTRACT,
        decision=AnalysisDecision.NOT_EXPENSE,
        reason="Message does not report a completed expense",
        transaction=None,
        missing_fields=[],
    )
    result = InboxProcessor(store, FakeAnalyser(output)).process_one(inbox_message)
    assert result.decision == "not_expense"
    assert store.list_review_items() == []
    assert read_ledger(store) == []


def test_private_store_files_are_owner_only(store) -> None:
    assert stat.S_IMODE(os.stat(store.database_path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(store.ledger_path).st_mode) == 0o600


def test_private_content_is_removed_from_persisted_reason(store, inbox_message) -> None:
    output = review_output().model_copy(
        update={
            "reason": (
                "Contact private@example.invalid about account 1234 5678 and quote "
                "Purchase completed at Example Store"
            )
        }
    )
    InboxProcessor(store, FakeAnalyser(output)).process_one(inbox_message)
    reason = store.list_review_items()[0].reason
    assert "private@example.invalid" not in reason
    assert "1234 5678" not in reason
    assert "Example Store" not in reason
