from __future__ import annotations

import sys
from datetime import date

import pytest
from test_review import FakeConsole
from test_spending import TODAY, expense, save

from mizu_kakeibo import cli
from mizu_kakeibo.correction import correct_spending, major_amount
from mizu_kakeibo.spending import parse_period
from mizu_kakeibo.storage import Store

MONTH = parse_period("month", TODAY)


@pytest.fixture
def store(tmp_path, inbox_message) -> Store:
    store = Store(tmp_path / "private.sqlite3", tmp_path / "ledger.csv")
    save(store, inbox_message, "m1", expense("GrabFood", 2490, date(2026, 10, 10)))
    save(store, inbox_message, "m2", expense("Grab Holdings Bhd", 2600, date(2026, 10, 17)))
    return store


def transactions(store: Store) -> list[tuple]:
    with store.connect() as connection:
        rows = connection.execute(
            "SELECT message_id, merchant, amount_minor, currency, transaction_date "
            "FROM transactions ORDER BY message_id"
        ).fetchall()
    return [tuple(row) for row in rows]


def correction_count(store: Store) -> int:
    with store.connect() as connection:
        return connection.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]


def removal_count(store: Store) -> int:
    with store.connect() as connection:
        return connection.execute("SELECT COUNT(*) FROM removals").fetchone()[0]


def test_a_picked_expense_takes_its_logged_values_and_saves_after_confirmation(store) -> None:
    console = FakeConsole(["2", "c", "Grab", "", "", "", "y", ""])

    assert correct_spending(store, MONTH, console) == 0
    assert console.output[0].splitlines() == [
        "October 2026",
        "",
        "  1  Sat 10 Oct  GrabFood           MYR 24.90",
        "  2  Sat 17 Oct  Grab Holdings Bhd  MYR 26.00",
        "",
        "  Total  MYR 50.90 · 2 expenses",
    ]
    assert console.prompts == [
        "Number to correct, or Enter to quit: ",
        "c correct · n not an expense · Enter back: ",
        "Merchant [Grab Holdings Bhd]: ",
        "Currency [MYR]: ",
        "Amount in MYR [26.00]: ",
        "Date [2026-10-17]: ",
        "Save? y/n: ",
        "Number to correct, or Enter to quit: ",
    ]
    assert "Picked  Sat 17 Oct  Grab Holdings Bhd  MYR 26.00" in console.output
    assert (
        "Was  Sat 17 Oct  Grab Holdings Bhd  MYR 26.00\n"
        "Now  Sat 17 Oct  Grab               MYR 26.00"
    ) in console.output
    assert "Saved." in console.output
    assert transactions(store) == [
        ("m1", "GrabFood", 2490, "MYR", "2026-10-10"),
        ("m2", "Grab", 2600, "MYR", "2026-10-17"),
    ]
    assert correction_count(store) == 1
    assert "m2,Grab,2600,MYR,2026-10-17" in store.ledger_path.read_text()
    lists = [text for text in console.output if text.startswith("October 2026")]
    assert len(lists) == 2
    assert "  2  Sat 17 Oct  Grab      MYR 26.00" in lists[1]


def test_a_new_currency_drops_the_amount_default_and_bad_values_are_asked_again(store) -> None:
    console = FakeConsole(["9", "x", "1", "c", "", "zzz", "jpy", "", "24.90", "2490", "17/10", ""])
    console.answers += ["y", "q"]

    assert correct_spending(store, MONTH, console) == 0
    assert console.output.count("  Type a number from 1 to 2, or press Enter to quit.") == 2
    assert "  'zzz' is not an ISO 4217 currency code, such as MYR." in console.output
    assert console.prompts.count("Amount in JPY, such as 1250: ") == 3
    assert "  JPY amounts have no decimal places." in console.output
    assert "  Type the date as YYYY-MM-DD." in console.output
    assert transactions(store)[0] == ("m1", "GrabFood", 2490, "JPY", "2026-10-10")


def test_a_date_moved_out_of_the_period_leaves_the_list(store) -> None:
    console = FakeConsole(["1", "c", "", "", "", "2026-09-30", "y", ""])

    assert correct_spending(store, MONTH, console) == 0
    lists = [text for text in console.output if text.startswith("October 2026")]
    assert "GrabFood" not in lists[1]
    assert "  1  Sat 17 Oct  Grab Holdings Bhd  MYR 26.00" in lists[1]


def test_unchanged_or_declined_values_save_nothing(store) -> None:
    console = FakeConsole(["1", "c", "", "", "", "", "1", "c", "Grab", "", "", "", "n", ""])

    assert correct_spending(store, MONTH, console) == 0
    assert "Nothing changed." in console.output
    assert "Not saved." in console.output
    assert correction_count(store) == 0
    assert transactions(store)[0] == ("m1", "GrabFood", 2490, "MYR", "2026-10-10")


def test_an_expense_removed_elsewhere_saves_nothing(store) -> None:
    def remove_elsewhere() -> str:
        with store.connect() as connection:
            connection.execute("DELETE FROM transactions WHERE message_id = 'm1'")
        return "y"

    console = FakeConsole(["1", "c", "Grab", "", "", "", remove_elsewhere, ""])

    assert correct_spending(store, MONTH, console) == 0
    assert "This expense is no longer logged; nothing was saved." in console.output
    assert correction_count(store) == 0


def test_not_an_expense_removes_it_after_confirmation(store) -> None:
    console = FakeConsole(["2", "x", "n", "y", ""])

    assert correct_spending(store, MONTH, console) == 0
    assert "  Press c, n, or Enter." in console.output
    assert "Not an expense: remove it from the ledger and every total" in console.output
    assert "Removed." in console.output
    assert transactions(store) == [("m1", "GrabFood", 2490, "MYR", "2026-10-10")]
    assert removal_count(store) == 1
    assert "m2" not in store.ledger_path.read_text()
    lists = [text for text in console.output if text.startswith("October 2026")]
    assert "Grab Holdings Bhd" not in lists[1]
    assert "  Total  MYR 24.90 · 1 expense" in lists[1]


def test_declining_or_going_back_removes_nothing(store) -> None:
    console = FakeConsole(["2", "n", "n", "2", "", ""])

    assert correct_spending(store, MONTH, console) == 0
    assert "Not saved." in console.output
    assert console.prompts.count("Number to correct, or Enter to quit: ") == 3
    assert removal_count(store) == 0
    assert len(transactions(store)) == 2


def test_an_expense_removed_elsewhere_is_not_removed_again(store) -> None:
    def remove_elsewhere() -> str:
        store.remove_transaction("gmail", "m1")
        return "y"

    console = FakeConsole(["1", "n", remove_elsewhere, ""])

    assert correct_spending(store, MONTH, console) == 0
    assert "This expense is no longer logged; nothing was saved." in console.output
    assert removal_count(store) == 1


@pytest.mark.parametrize("leave", ["q", EOFError(), KeyboardInterrupt()])
def test_leaving_at_any_prompt_saves_nothing(store, leave) -> None:
    console = FakeConsole(["1", "c", "Grab", "", "", leave])

    assert correct_spending(store, MONTH, console) == 0
    assert console.prompts[-1] == "Date [2026-10-10]: "
    assert correction_count(store) == 0
    assert transactions(store)[0][1] == "GrabFood"


def test_without_a_terminal_it_prints_the_list_as_before_and_asks_nothing(store) -> None:
    console = FakeConsole([], interactive=False)

    assert correct_spending(store, MONTH, console) == 0
    assert console.prompts == []
    assert console.output[0].splitlines()[2] == "  Sat 10 Oct  GrabFood           MYR 24.90"


def test_an_empty_period_asks_nothing(store) -> None:
    console = FakeConsole([])

    assert correct_spending(store, parse_period("2026-01", TODAY), console) == 0
    assert console.output == ["January 2026\n\n  No expenses logged."]
    assert console.prompts == []


@pytest.mark.parametrize(
    ("minor", "currency", "text"),
    [
        (2490, "MYR", "24.90"),
        (123405, "MYR", "1234.05"),
        (1250, "JPY", "1250"),
        (5, "KWD", "0.005"),
    ],
)
def test_the_amount_default_is_typed_in_the_major_unit(minor, currency, text) -> None:
    assert major_amount(minor, currency) == text


def test_spend_corrects_only_in_a_terminal_and_not_by_merchant(
    private_live_paths, inbox_message, monkeypatch, capsys
) -> None:
    store = Store(private_live_paths / "kakeibo.sqlite3", private_live_paths / "ledger.csv")
    save(store, inbox_message, "m1", expense("GrabFood", 2490, date(2026, 10, 10)))
    monkeypatch.setattr(cli, "_today", lambda: TODAY)
    console = FakeConsole(["1", "c", "Grab", "", "", "", "y", ""])
    monkeypatch.setattr(cli, "Console", lambda: console)

    assert cli.main(["spend", "month"]) == 0
    assert console.prompts == []
    assert "  Sat 10 Oct  GrabFood  MYR 24.90" in capsys.readouterr().out

    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert cli.main(["spend", "month", "--by", "merchant"]) == 0
    assert console.prompts == []
    assert cli.main(["spend", "month"]) == 0
    assert "Saved." in console.output
    assert transactions(store) == [("m1", "Grab", 2490, "MYR", "2026-10-10")]
