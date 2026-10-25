from __future__ import annotations

import sqlite3
from datetime import date

import pytest
from test_review import FakeConsole

from mizu_kakeibo import cli
from mizu_kakeibo.constants import ANALYSIS_OUTPUT_CONTRACT
from mizu_kakeibo.flags import find_flags, fold_merchant, is_possible_duplicate
from mizu_kakeibo.models import (
    AnalysisDecision,
    ExpenseAnalysisOutput,
    MessageSource,
    MissingField,
    Transaction,
)
from mizu_kakeibo.ollama import RuntimeMetadata
from mizu_kakeibo.review import review
from mizu_kakeibo.spending import render_status
from mizu_kakeibo.storage import Store

METADATA = RuntimeMetadata(
    runtime="ollama",
    runtime_version="0.40.5",
    model="qwen3.5:4b-q4_K_M",
    model_digest="sha256-test",
    prompt_version="expense-v0.1",
    temperature=0,
    seed=42,
    context_size=32768,
    max_generated_tokens=512,
)
TODAY = date(2026, 10, 18)


def expense(merchant: str, amount_minor: int, day: date, currency: str = "MYR") -> Transaction:
    return Transaction(
        merchant=merchant, amount_minor=amount_minor, currency=currency, transaction_date=day
    )


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "private.sqlite3", tmp_path / "ledger.csv")


@pytest.fixture
def save(store, inbox_message):
    def save(
        message_id: str, transaction: Transaction | None, *, check_unusual: bool = True
    ) -> int:
        envelope = inbox_message.model_copy(
            update={
                "source": MessageSource(
                    provider="gmail", message_id=message_id, thread_id=message_id
                ),
                "subject": f"Fictional receipt {message_id}",
            }
        )
        output = ExpenseAnalysisOutput(
            contract_version=ANALYSIS_OUTPUT_CONTRACT,
            decision=AnalysisDecision.EXPENSE if transaction else AnalysisDecision.NEEDS_REVIEW,
            reason="Fictional decision",
            transaction=transaction,
            missing_fields=[] if transaction else [MissingField.AMOUNT_MINOR],
        )
        return store.save_analysis(envelope, output, METADATA, check_unusual=check_unusual)

    return save


def flagged(store: Store) -> list[tuple[str, tuple[str, ...]]]:
    return [
        (flag.entry.message_id, flag.kinds) for flag in find_flags(store.list_flag_candidates())
    ]


def before_the_feature(store: Store, analysis_id: int) -> None:
    """Make an analysis look saved before flags existed, when nothing recorded its checks."""
    with store.connect() as connection:
        connection.execute("DELETE FROM flag_checks WHERE analysis_id = ?", (analysis_id,))


def test_a_duplicate_is_the_same_merchant_amount_currency_and_day() -> None:
    charge = expense("Kopi  Corner", 1250, TODAY)
    assert is_possible_duplicate(charge, expense("kopi corner", 1250, TODAY))
    assert not is_possible_duplicate(charge, expense("Kopi Corner", 1250, date(2026, 10, 17)))
    assert not is_possible_duplicate(charge, expense("Kopi Corner", 1250, TODAY, "SGD"))
    assert not is_possible_duplicate(charge, expense("Kopi Corner", 1251, TODAY))
    assert not is_possible_duplicate(charge, expense("Kopi Corners", 1250, TODAY))
    assert fold_merchant("Ｋｏｐｉ\tCorner ") == "kopi corner"


def test_only_the_later_logged_of_a_same_day_pair_is_flagged(store, save) -> None:
    save("m1", expense("Kopi Corner", 1250, TODAY))
    save("m2", expense("kopi corner", 1250, TODAY))
    assert flagged(store) == [("m2", ("duplicate",))]
    # A flag never changes or removes an expense.
    assert len(store.list_transactions(TODAY, TODAY)) == 2


def test_a_different_day_or_currency_is_not_a_duplicate(store, save) -> None:
    save("m1", expense("Kopi Corner", 1250, TODAY))
    save("m2", expense("Kopi Corner", 1250, date(2026, 10, 17)))
    save("m3", expense("Kopi Corner", 1250, TODAY, "SGD"))
    assert flagged(store) == []


def test_an_amount_is_unusual_only_after_three_earlier_charges(store, save) -> None:
    save("m1", expense("Kopi Corner", 1000, date(2026, 10, 1)))
    save("m2", expense("Kopi Corner", 1200, date(2026, 10, 2)))
    save("m3", expense("Kopi Corner", 5000, TODAY))
    assert flagged(store) == []

    save("m4", expense("Kopi Corner", 900, date(2026, 10, 3)))
    [flag] = find_flags(store.list_flag_candidates())
    assert (flag.entry.message_id, flag.kinds) == ("m3", ("unusual",))
    assert (flag.usual_minor, flag.earlier_charges) == (1000, 3)


def test_unusual_needs_more_than_twice_the_median_in_the_same_currency(store, save) -> None:
    for number, day in enumerate((1, 2, 3), start=1):
        save(f"m{number}", expense("Kopi Corner", 1000, date(2026, 10, day)))
    save("sgd", expense("Kopi Corner", 9000, date(2026, 10, 4), "SGD"))
    save("double", expense("Kopi Corner", 2000, TODAY))
    assert flagged(store) == []


def test_keeping_clears_the_flag_for_good(store, save) -> None:
    save("m1", expense("Kopi Corner", 1250, TODAY))
    save("m2", expense("Kopi Corner", 1250, TODAY))
    [flag] = find_flags(store.list_flag_candidates())
    store.keep_flags(flag.candidate.analysis_id, flag.kinds)
    assert flagged(store) == []

    # A correction keeps the analysis, so the kept flag does not return.
    store.correct_transaction("gmail", "m2", amount_minor=1300)
    store.correct_transaction("gmail", "m2", amount_minor=1250)
    assert flagged(store) == []


def test_correcting_rechecks_the_flag(store, save) -> None:
    save("m1", expense("Kopi Corner", 1250, TODAY))
    save("m2", expense("Kopi Corner", 1250, TODAY))
    store.correct_transaction("gmail", "m2", amount_minor=1350)
    assert flagged(store) == []

    save("m3", expense("Other Shop", 1250, TODAY))
    store.correct_transaction("gmail", "m3", merchant="Kopi Corner")
    assert flagged(store) == [("m3", ("duplicate",))]


def test_removing_the_flagged_expense_clears_the_pair(store, save) -> None:
    save("m1", expense("Kopi Corner", 1250, TODAY))
    save("m2", expense("Kopi Corner", 1250, TODAY))
    store.remove_transaction("gmail", "m2")
    assert flagged(store) == []


def test_expenses_logged_before_the_feature_are_not_flagged(store, save) -> None:
    for number in range(1, 4):
        save(f"old{number}", expense("Kopi Corner", 1000, date(2026, 10, number)))
    before_the_feature(store, save("m1", expense("Kopi Corner", 1250, TODAY)))
    before_the_feature(store, save("m2", expense("Kopi Corner", 5000, TODAY)))
    assert flagged(store) == []

    # A pre-feature expense still counts as the earlier one of a new pair.
    save("m3", expense("Kopi Corner", 1250, TODAY))
    assert flagged(store) == [("m3", ("duplicate",))]


def test_a_backfill_checks_duplicates_but_not_unusual_amounts(store, save) -> None:
    for number in range(1, 4):
        save(f"m{number}", expense("Kopi Corner", 1000, date(2026, 10, number)))
    save("big", expense("Kopi Corner", 5000, TODAY), check_unusual=False)
    save("again", expense("Kopi Corner", 5000, TODAY), check_unusual=False)
    assert flagged(store) == [("again", ("duplicate",))]


def test_an_expense_can_be_both_a_duplicate_and_unusual(store, save) -> None:
    for number in range(1, 4):
        save(f"m{number}", expense("Kopi Corner", 1000, date(2026, 10, number)))
    save("big", expense("Kopi Corner", 5000, TODAY))
    save("again", expense("Kopi Corner", 5000, TODAY))
    assert flagged(store) == [("again", ("duplicate", "unusual")), ("big", ("unusual",))]


def test_a_resolved_review_item_is_checked_from_its_resolution(store, save) -> None:
    save("m1", expense("Kopi Corner", 1250, TODAY))
    save("m2", None)
    before_the_feature(store, save("m3", None))
    store.resolve_review(
        "gmail", "m2", AnalysisDecision.EXPENSE, expense("Kopi Corner", 1250, TODAY)
    )
    store.resolve_review(
        "gmail", "m3", AnalysisDecision.EXPENSE, expense("Kopi Corner", 1250, TODAY)
    )
    assert flagged(store) == [("m2", ("duplicate",))]


def test_reevaluating_rechecks_the_flag(store, save, inbox_message) -> None:
    save("m1", expense("Kopi Corner", 1250, TODAY))
    save("m2", expense("Kopi Corner", 1250, TODAY))
    [flag] = find_flags(store.list_flag_candidates())
    store.keep_flags(flag.candidate.analysis_id, flag.kinds)
    envelope = store.message("gmail", "m2")
    output = ExpenseAnalysisOutput(
        contract_version=ANALYSIS_OUTPUT_CONTRACT,
        decision=AnalysisDecision.EXPENSE,
        reason="Fictional decision",
        transaction=expense("Kopi Corner", 1250, TODAY),
        missing_fields=[],
    )
    store.save_analysis(envelope, output, METADATA, reevaluate=True)
    assert flagged(store) == [("m2", ("duplicate",))]


def test_an_existing_database_gains_the_flag_tables(tmp_path, save, store) -> None:
    save("m1", expense("Kopi Corner", 1250, TODAY))
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("DROP TABLE flag_keeps")
        connection.execute("DROP TABLE flag_checks")
    reopened = Store(store.database_path, store.ledger_path)
    assert flagged(reopened) == []
    assert len(reopened.list_transactions(TODAY, TODAY)) == 1


def test_status_shows_flags_only_while_some_wait(save, store) -> None:
    entries = store.list_transactions(TODAY, TODAY)
    without = render_status(TODAY, entries, 0, None)
    assert render_status(TODAY, entries, 0, None, flag_count=0) == without
    assert "Flags" not in without
    status = render_status(TODAY, entries, 0, None, flag_count=2)
    assert "  To review   nothing\n  Flags       2 to check\n  Last sync" in status
    assert status.replace("  Flags       2 to check\n", "") == without


def test_the_bare_command_counts_the_flags(
    private_live_paths, inbox_message, monkeypatch, capsys
) -> None:
    store = Store(private_live_paths / "kakeibo.sqlite3", private_live_paths / "ledger.csv")
    for message_id in ("m1", "m2"):
        envelope = inbox_message.model_copy(
            update={"source": MessageSource(provider="gmail", message_id=message_id, thread_id="t")}
        )
        output = ExpenseAnalysisOutput(
            contract_version=ANALYSIS_OUTPUT_CONTRACT,
            decision=AnalysisDecision.EXPENSE,
            reason="Fictional decision",
            transaction=expense("Kopi Corner", 1250, TODAY),
            missing_fields=[],
        )
        store.save_analysis(envelope, output, METADATA)
    monkeypatch.setattr(cli, "_today", lambda: TODAY)
    assert cli.main([]) == 0
    assert "  To review   nothing\n  Flags       1 to check\n" in capsys.readouterr().out


def test_review_lists_flags_after_the_review_items(store, save) -> None:
    save("r1", None)
    save("m1", expense("Kopi Corner", 1250, TODAY))
    save("m2", expense("Kopi Corner", 1250, TODAY))
    console = FakeConsole([], interactive=False)
    assert review(store, console) == 0
    assert console.text.endswith(
        "1 flagged expense to check\n\n  2  Sun 18 Oct  Kopi Corner  MYR 12.50  possible duplicate"
    )
    assert console.text.startswith("1 message to review\n\n  1  ")


def test_keeping_in_review_clears_the_flag(store, save) -> None:
    save("m1", expense("Kopi Corner", 1250, TODAY))
    save("m2", expense("Kopi Corner", 1250, TODAY))
    console = FakeConsole(["1", "x", "k", "n", "k", "y"])
    assert review(store, console) == 0
    assert "Fictional receipt m2" in console.text
    assert "Earlier   Example Store, Fictional receipt m1" in console.text
    assert "  Press k, n, g, s, or q." in console.output
    assert "Not saved." in console.output
    assert console.output[-2:] == ["", "Nothing to review."]
    assert flagged(store) == []
    assert len(store.list_transactions(TODAY, TODAY)) == 2


def test_not_an_expense_in_review_removes_it(store, save) -> None:
    save("1a", expense("Kopi Corner", 1250, TODAY))
    save("1b", expense("Kopi Corner", 1250, TODAY))
    console = FakeConsole(["1", "g", "n", "y"])
    assert review(store, console) == 0
    assert console.opened == ["https://mail.google.com/mail/#all/thread-f:27|msg-f:27"]
    assert "Removed." in console.output
    assert [entry.message_id for entry in store.list_transactions(TODAY, TODAY)] == ["1a"]
    assert flagged(store) == []


def test_review_passes_over_a_flag_cleared_in_the_meantime(store, save) -> None:
    save("m1", expense("Kopi Corner", 1250, TODAY))
    save("m2", expense("Kopi Corner", 1250, TODAY))
    save("m3", expense("Kopi Corner", 1250, TODAY))

    def remove_m3() -> str:
        store.remove_transaction("gmail", "m3")
        return "s"

    console = FakeConsole(["1", remove_m3, "q"])
    assert review(store, console) == 0
    assert "Expense 2 is no longer flagged; passing over it." in console.output
