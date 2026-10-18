from __future__ import annotations

from datetime import date

import pytest

from mizu_kakeibo import cli
from mizu_kakeibo.constants import ANALYSIS_OUTPUT_CONTRACT
from mizu_kakeibo.models import (
    AnalysisDecision,
    ExpenseAnalysisOutput,
    MessageSource,
    MissingField,
    Transaction,
)
from mizu_kakeibo.ollama import RuntimeMetadata
from mizu_kakeibo.spending import (
    format_money,
    last_sync,
    parse_period,
    render_spending,
    render_status,
)
from mizu_kakeibo.storage import LedgerEntry, Store

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


def entry(merchant: str, amount_minor: int, day: date, currency: str = "MYR") -> LedgerEntry:
    return LedgerEntry("gmail", f"{merchant}-{day}", merchant, amount_minor, currency, day)


def save(store: Store, message, message_id: str, transaction: Transaction | None) -> None:
    envelope = message.model_copy(
        update={"source": MessageSource(provider="gmail", message_id=message_id, thread_id="t")}
    )
    output = ExpenseAnalysisOutput(
        contract_version=ANALYSIS_OUTPUT_CONTRACT,
        decision=AnalysisDecision.EXPENSE if transaction else AnalysisDecision.NEEDS_REVIEW,
        reason="Fictional decision",
        transaction=transaction,
        missing_fields=[] if transaction else [MissingField.AMOUNT_MINOR],
    )
    store.save_analysis(envelope, output, METADATA)


def expense(merchant: str, amount_minor: int, day: date, currency: str = "MYR") -> Transaction:
    return Transaction(
        merchant=merchant, amount_minor=amount_minor, currency=currency, transaction_date=day
    )


@pytest.mark.parametrize(
    ("value", "start", "end", "label"),
    [
        ("today", TODAY, TODAY, "Today, Sun 18 Oct 2026"),
        ("yesterday", date(2026, 10, 17), date(2026, 10, 17), "Yesterday, Sat 17 Oct 2026"),
        ("week", date(2026, 10, 12), date(2026, 10, 18), "This week, Mon 12 Oct – Sun 18 Oct 2026"),
        ("last-week", date(2026, 10, 5), date(2026, 10, 11), None),
        ("month", date(2026, 10, 1), date(2026, 10, 31), "October 2026"),
        ("last-month", date(2026, 9, 1), date(2026, 9, 30), "September 2026"),
        ("year", date(2026, 1, 1), date(2026, 12, 31), "2026"),
        ("2026-02-03", date(2026, 2, 3), date(2026, 2, 3), "Tue 3 Feb 2026"),
        ("2024-02", date(2024, 2, 1), date(2024, 2, 29), "February 2024"),
        ("2025", date(2025, 1, 1), date(2025, 12, 31), "2025"),
    ],
)
def test_parse_period_resolves_names_and_dates_against_today(value, start, end, label) -> None:
    period = parse_period(value, TODAY)
    assert (period.start, period.end) == (start, end)
    if label is not None:
        assert period.label == label


def test_last_month_in_january_is_the_previous_december() -> None:
    period = parse_period("last-month", date(2027, 1, 15))
    assert (period.start, period.end) == (date(2026, 12, 1), date(2026, 12, 31))


@pytest.mark.parametrize("value", ["", "lastmonth", "2026-13", "2026-10-32", "26"])
def test_parse_period_rejects_unknown_periods(value) -> None:
    with pytest.raises(ValueError, match="period must be one of"):
        parse_period(value, TODAY)


def test_format_money_uses_each_currency_minor_unit() -> None:
    assert format_money(2490, "MYR") == "MYR 24.90"
    assert format_money(5, "MYR") == "MYR 0.05"
    assert format_money(123456789, "MYR") == "MYR 1,234,567.89"
    assert format_money(1500, "JPY") == "JPY 1,500"
    assert format_money(12345, "BHD") == "BHD 12.345"


def test_spending_lists_expenses_and_totals_each_currency_without_converting() -> None:
    entries = [
        entry("GrabFood", 2490, date(2026, 10, 10)),
        entry("Netflix", 1599, date(2026, 10, 12), "USD"),
        entry("GrabFood", 2190, date(2026, 10, 11)),
    ]
    rendered = render_spending(parse_period("month", TODAY), entries, by_merchant=False)
    assert rendered.splitlines() == [
        "October 2026",
        "",
        "  Sat 10 Oct  GrabFood  MYR 24.90",
        "  Mon 12 Oct  Netflix   USD 15.99",
        "  Sun 11 Oct  GrabFood  MYR 21.90",
        "",
        "  Total  MYR 46.80 + USD 15.99 · 3 expenses",
        "  Each currency is totalled separately, not converted to MYR.",
    ]


def test_spending_by_merchant_groups_and_orders_by_total() -> None:
    entries = [
        entry("foodpanda", 2446, date(2026, 10, 14)),
        entry("GrabFood", 2490, date(2026, 10, 10)),
        entry("GrabFood", 2190, date(2026, 10, 11)),
        entry("A merchant whose name is far too long to fit", 100, date(2026, 10, 12)),
    ]
    rendered = render_spending(parse_period("month", TODAY), entries, by_merchant=True)
    assert rendered.splitlines()[2:7] == [
        "  GrabFood                          2×  MYR 46.80",
        "  foodpanda                         1×  MYR 24.46",
        "  A merchant whose name is far to…  1×   MYR 1.00",
        "",
        "  Total  MYR 72.26 · 4 expenses",
    ]


def test_spending_says_when_nothing_was_logged() -> None:
    rendered = render_spending(parse_period("today", TODAY), [], by_merchant=False)
    assert rendered == "Today, Sun 18 Oct 2026\n\n  No expenses logged.\n"


def test_status_totals_today_week_and_month() -> None:
    entries = [
        entry("Domino's", 3590, date(2026, 10, 1)),
        entry("GrabFood", 2190, date(2026, 10, 12)),
        entry("Grab", 2400, TODAY),
    ]
    lines = render_status(TODAY, entries, 2, "Sun 18 Oct 01:38 · 1 expense").splitlines()
    assert lines[:7] == [
        "Mizu Kakeibo · Sun 18 Oct 2026",
        "",
        "  Today       MYR 24.00 · 1 expense",
        "  This week   MYR 45.90 · 2 expenses",
        "  This month  MYR 81.80 · 3 expenses",
        "  To review   2 items",
        "  Last sync   Sun 18 Oct 01:38 · 1 expense",
    ]
    assert lines[-1].split()[:2] == ["mizu-kakeibo", "review"]


def test_last_sync_skips_gated_runs_and_describes_the_newest_attempt(tmp_path) -> None:
    log = tmp_path / "scheduled.log"
    assert last_sync(log) is None
    log.write_text(
        "2026-10-18T01:38:22+08:00 trigger=launchd outcome=synced run=20261018-013822 "
        "expense=1 not_expense=1 needs_review=2 skipped=15 filtered=24 failed=0 deferred=0 "
        "day_done=yes\n"
        "2026-10-18T09:00:06+08:00 trigger=launchd outcome=done_today\n",
        encoding="utf-8",
    )
    assert last_sync(log) == "Sun 18 Oct 01:38 · 1 expense, 2 to review, 0 failed"
    with log.open("a", encoding="utf-8") as target:
        target.write(
            "2026-10-19T08:00:03+08:00 trigger=launchd outcome=failed run=x stage=sign_in\n"
            "2026-10-19T09:00:03+08:00 trigger=launchd outcome=low_power\n"
        )
    assert last_sync(log) == "Mon 19 Oct 08:00 · stopped at the Gmail sign-in"


def test_bare_command_shows_status_from_the_live_files(
    private_live_paths, inbox_message, monkeypatch, capsys
) -> None:
    store = Store(private_live_paths / "kakeibo.sqlite3", private_live_paths / "ledger.csv")
    save(store, inbox_message, "m1", expense("Grab", 2400, TODAY))
    save(store, inbox_message, "m2", expense("GrabFood", 2190, date(2026, 10, 11)))
    save(store, inbox_message, "m3", expense("Older", 999, date(2026, 9, 30)))
    save(store, inbox_message, "m4", None)
    monkeypatch.setattr(cli, "_today", lambda: TODAY)

    assert cli.main([]) == 0
    output = capsys.readouterr().out
    assert "  Today       MYR 24.00 · 1 expense\n" in output
    assert "  This week   MYR 24.00 · 1 expense\n" in output
    assert "  This month  MYR 45.90 · 2 expenses\n" in output
    assert "  To review   1 item\n" in output
    assert "  Last sync   none recorded\n" in output


def test_spend_reads_the_period_from_the_live_files(
    private_live_paths, inbox_message, monkeypatch, capsys
) -> None:
    store = Store(private_live_paths / "kakeibo.sqlite3", private_live_paths / "ledger.csv")
    save(store, inbox_message, "m1", expense("Grab", 2400, TODAY))
    save(store, inbox_message, "m2", expense("Older", 999, date(2026, 9, 30)))
    monkeypatch.setattr(cli, "_today", lambda: TODAY)

    assert cli.main(["spend", "last-month"]) == 0
    assert capsys.readouterr().out == (
        "September 2026\n\n  Wed 30 Sep  Older  MYR 9.99\n\n  Total  MYR 9.99 · 1 expense\n"
    )
    assert cli.main(["spend"]) == 0
    assert "Grab  MYR 24.00" in capsys.readouterr().out


def test_reading_commands_do_not_create_a_missing_database(private_live_paths, capsys) -> None:
    for command in (["status"], ["spend", "month"], ["review"]):
        with pytest.raises(SystemExit):
            cli.main(command)
        assert "no database at" in capsys.readouterr().err
    assert not private_live_paths.exists()
