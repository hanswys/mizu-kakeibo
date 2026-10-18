from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest

from mizu_kakeibo import cli
from mizu_kakeibo.constants import ANALYSIS_OUTPUT_CONTRACT
from mizu_kakeibo.models import (
    AnalysisDecision,
    ExpenseAnalysisOutput,
    MessageContent,
    MessageSource,
    MissingField,
)
from mizu_kakeibo.ollama import RuntimeMetadata
from mizu_kakeibo.review import parse_amount, review
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
# 2026-10-18 02:30 in Malaysia, the day after in UTC.
RECEIVED = datetime(2026, 10, 17, 18, 30, tzinfo=timezone.utc)
BODY = "\n\n".join(f"Fictional line {number}" for number in range(1, 21))


class FakeConsole:
    """Answer each prompt from a script; a callable answer runs first and returns the answer."""

    def __init__(self, answers, *, interactive=True) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.output: list[str] = []
        self.paged: list[str] = []
        self.opened: list[str] = []
        self.tty = interactive

    def interactive(self) -> bool:
        return self.tty

    def width(self) -> int:
        return 100

    def write(self, text: str = "") -> None:
        self.output.append(text)

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer() if callable(answer) else answer

    def page(self, text: str) -> None:
        self.paged.append(text)

    def open(self, url: str) -> None:
        self.opened.append(url)

    @property
    def text(self) -> str:
        return "\n".join(self.output)


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "private.sqlite3", tmp_path / "ledger.csv")


def add(
    store: Store,
    inbox_message,
    message_id: str,
    *,
    sender: str = "Example Store <receipts@example.invalid>",
    subject: str = "Your receipt",
    text: str = BODY,
    missing: tuple[MissingField, ...] = (MissingField.AMOUNT_MINOR,),
) -> None:
    envelope = inbox_message.model_copy(
        update={
            "source": MessageSource(provider="gmail", message_id=message_id, thread_id=message_id),
            "received_at": RECEIVED,
            "sender": sender,
            "subject": subject,
            "content": MessageContent(text=text, source_type="text_plain", truncated=False),
        }
    )
    output = ExpenseAnalysisOutput(
        contract_version=ANALYSIS_OUTPUT_CONTRACT,
        decision=AnalysisDecision.NEEDS_REVIEW,
        reason="Fictional unsure purchase",
        transaction=None,
        missing_fields=list(missing),
    )
    store.save_analysis(envelope, output, METADATA)


def resolutions(store: Store) -> list[tuple]:
    with store.connect() as connection:
        rows = connection.execute(
            "SELECT a.message_id, r.decision, r.merchant, r.amount_minor, r.currency, "
            "r.transaction_date FROM resolutions r JOIN analyses a ON a.id = r.analysis_id "
            "ORDER BY r.id"
        ).fetchall()
    return [tuple(row) for row in rows]


def test_the_list_and_an_item_show_the_message_without_saving(store, inbox_message) -> None:
    add(store, inbox_message, "1a")
    add(
        store,
        inbox_message,
        "2b",
        sender="no-reply@example.invalid",
        subject="A fictional subject long enough that it has to be shortened to fit one line "
        "of the terminal",
        missing=(),
    )
    console = FakeConsole(["1", "m", "g", "q"])

    assert review(store, console) == 0
    listing = console.output[0].splitlines()
    assert listing[:2] == ["2 messages to review", ""]
    first, second = listing[2:]
    assert first.startswith("  1  Sun 18 Oct  Example Store         Your receipt  ")
    assert first.endswith("  missing amount")
    assert second.startswith("  2  Sun 18 Oct  no-reply@example.in…  A fictional subject")
    assert second.endswith("…  Fictional unsure purchase")
    assert all(len(line) <= 100 for line in console.text.splitlines())

    item = console.text.split("Your receipt\n", 2)[-1]
    assert "  From      Example Store <receipts@example.invalid>\n" in item
    assert "  Received  2026-10-18 02:30\n" in item
    assert "  Reason    Fictional unsure purchase\n" in item
    assert "  Missing   amount\n" in item
    assert "    Fictional line 15\n" in item
    assert "Fictional line 16" not in item
    assert "    … 5 more lines, m shows the whole text" in item
    assert console.paged == [BODY + "\n"]
    assert console.opened == ["https://mail.google.com/mail/#all/thread-f:26|msg-f:26"]
    assert resolutions(store) == []


def test_an_expense_takes_its_defaults_and_saves_after_confirmation(store, inbox_message) -> None:
    add(store, inbox_message, "1a")
    console = FakeConsole(["1", "e", "", "", "12.50", "", "y"])

    assert review(store, console) == 0
    assert console.prompts[2:6] == [
        "Merchant [Example Store]: ",
        "Currency [MYR]: ",
        "Amount in MYR, such as 12.50: ",
        "Date [2026-10-18]: ",
    ]
    assert "Expense  Sun 18 Oct  Example Store  MYR 12.50" in console.output
    assert resolutions(store) == [
        ("1a", "expense", "Example Store", 1250, "MYR", "2026-10-18"),
    ]
    assert "Saved." in console.output
    assert console.output[-1] == "Nothing to review."
    assert "1a,Example Store,1250,MYR,2026-10-18" in store.ledger_path.read_text()


def test_invalid_values_are_explained_and_asked_again(store, inbox_message) -> None:
    add(store, inbox_message, "1a", sender="receipts@example.invalid")
    console = FakeConsole(
        ["1", "e", "", " Grab ", "zzz", "jpy", "12.50", "0", "1,250", "18/10/2026", "2026-10-01"]
        + ["n", "n", "y"]
    )

    assert review(store, console) == 0
    assert console.prompts[2] == "Merchant: "
    assert "  The merchant can't be blank." in console.output
    assert "  'zzz' is not an ISO 4217 currency code, such as MYR." in console.output
    assert console.prompts.count("Amount in JPY, such as 1250: ") == 3
    assert "  JPY amounts have no decimal places." in console.output
    assert "  The amount must be more than zero." in console.output
    assert "  Type the date as YYYY-MM-DD." in console.output
    assert "Expense  Thu  1 Oct  Grab  JPY 1,250" in console.output
    assert "Not an expense" in console.output
    assert resolutions(store) == [("1a", "not_expense", None, None, None, None)]


@pytest.mark.parametrize(
    ("text", "currency", "minor"),
    [
        ("12.5", "MYR", 1250),
        ("1,234.05", "MYR", 123405),
        ("12.345", "KWD", 12345),
        ("7", "MYR", 700),
    ],
)
def test_amounts_are_typed_in_the_major_unit(text, currency, minor) -> None:
    assert parse_amount(text, currency) == minor


@pytest.mark.parametrize("text", ["12,50", "12.505", "-1", "MYR 12", ".50", "1,2345"])
def test_amounts_that_are_not_receipt_amounts_are_rejected(text) -> None:
    with pytest.raises(ValueError):
        parse_amount(text, "MYR")


def test_the_next_item_follows_and_the_list_is_read_afresh(store, inbox_message) -> None:
    add(store, inbox_message, "1a", subject="First")
    add(store, inbox_message, "2b", subject="Second")
    add(store, inbox_message, "3c", subject="Third")

    def resolve_second_elsewhere() -> str:
        store.resolve_review("gmail", "2b", AnalysisDecision.NOT_EXPENSE)
        add(store, inbox_message, "4d", subject="Fourth")  # a sync adds an item meanwhile
        return "s"

    console = FakeConsole(["2", resolve_second_elsewhere, "n", "y", "q"])

    assert review(store, console) == 0
    shown = [text.split("\n")[0] for text in console.output if "\n  From " in text]
    assert shown == ["Second", "Third"]
    assert resolutions(store) == [
        ("2b", "not_expense", None, None, None, None),
        ("3c", "not_expense", None, None, None, None),
    ]
    lists = [text for text in console.output if "messages to review" in text]
    assert len(lists) == 2
    assert "Second" not in lists[1] and "Third" not in lists[1]
    assert "First" in lists[1] and "Fourth" in lists[1]


def test_an_item_resolved_elsewhere_is_passed_over(store, inbox_message) -> None:
    add(store, inbox_message, "1a", subject="First")
    add(store, inbox_message, "2b", subject="Second")
    add(store, inbox_message, "3c", subject="Third")

    def resolve_second_elsewhere() -> str:
        store.resolve_review("gmail", "2b", AnalysisDecision.NOT_EXPENSE)
        return "s"

    def resolve_third_elsewhere() -> str:
        store.resolve_review("gmail", "3c", AnalysisDecision.NOT_EXPENSE)
        return "y"

    console = FakeConsole(["1", resolve_second_elsewhere, "n", resolve_third_elsewhere, "q"])

    assert review(store, console) == 0
    assert "Message 2 was already resolved; passing over it." in console.output
    assert "This message was already resolved elsewhere; nothing was saved." in console.output
    assert [row[0] for row in resolutions(store)] == ["2b", "3c"]


@pytest.mark.parametrize("leave", ["q", EOFError(), KeyboardInterrupt()])
def test_leaving_at_any_prompt_saves_nothing(store, inbox_message, leave) -> None:
    add(store, inbox_message, "1a")
    console = FakeConsole(["1", "e", "", "", "12.50", leave])

    assert review(store, console) == 0
    assert console.prompts[-1] == "Date [2026-10-18]: "
    assert resolutions(store) == []


def test_message_text_cannot_drive_the_terminal(store, inbox_message) -> None:
    add(
        store,
        inbox_message,
        "1a",
        sender="\x1b]0;Evil\x07Shop <shop@example.invalid>",
        subject="Receipt\x1b[2J‮",
        text="Paid\x1b[31m MYR 9\r\n",
    )
    console = FakeConsole(["1", "m", "q"])

    assert review(store, console) == 0
    for text in [*console.output, *console.paged]:
        assert "\x1b" not in text and "\x07" not in text and "‮" not in text
    assert "    Paid[31m MYR 9" in console.text


def test_without_a_terminal_it_prints_the_list_and_asks_nothing(store, inbox_message) -> None:
    add(store, inbox_message, "1a")
    console = FakeConsole([], interactive=False)

    assert review(store, console) == 0
    assert console.prompts == []
    assert console.output[0].startswith("1 message to review\n\n  1  Sun 18 Oct")


def test_the_review_command_uses_the_live_files(
    private_live_paths, inbox_message, monkeypatch, capsys
) -> None:
    store = Store(private_live_paths / "kakeibo.sqlite3", private_live_paths / "ledger.csv")
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert cli.main(["review"]) == 0
    assert capsys.readouterr().out == "Nothing to review.\n"

    add(store, inbox_message, "1a")
    assert cli.main(["review"]) == 0
    assert "1 message to review" in capsys.readouterr().out
    assert resolutions(store) == []
