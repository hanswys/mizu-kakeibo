from __future__ import annotations

import pydoc
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from email.utils import parseaddr
from typing import TypeVar

from iso4217 import Currency

from .models import AnalysisDecision, InboxMessage, Transaction
from .scheduled import MALAYSIA
from .spending import HOME_CURRENCY, expense_row
from .storage import ReviewItem, ReviewResolutionError, Store

PREVIEW_LINES = 15
SENDER_WIDTH = 20
WHY_WIDTH = 30
FIELD_NAMES = {
    "merchant": "merchant",
    "amount_minor": "amount",
    "currency": "currency",
    "transaction_date": "date",
    "message_content": "text",
}
KEYS = "e expense · n not an expense · m whole text · g Gmail · s skip · q quit: "
# Control characters and bidirectional overrides, so a message cannot drive the terminal.
_UNSAFE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f‪-‮⁦-⁩]")
_AMOUNT = re.compile(r"(\d+|\d{1,3}(?:,\d{3})+)(?:\.(\d+))?")
T = TypeVar("T")


def gmail_url(message_id: str, thread_id: str | None) -> str | None:
    """Build the Gmail web link the connector reports, from hexadecimal Gmail IDs."""
    try:
        message = int(message_id, 16)
        thread = int(thread_id, 16) if thread_id else None
    except ValueError:
        return None
    if thread is None:
        return None
    return f"https://mail.google.com/mail/#all/thread-f:{thread}|msg-f:{message}"


def item_url(item: ReviewItem) -> str | None:
    return gmail_url(item.message_id, item.thread_id) if item.provider == "gmail" else None


class Console:
    """The terminal a review reads from and writes to."""

    def interactive(self) -> bool:
        return sys.stdin.isatty()

    def width(self) -> int:
        return shutil.get_terminal_size().columns

    def write(self, text: str = "") -> None:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()

    def ask(self, prompt: str) -> str:
        return input(prompt)

    def page(self, text: str) -> None:
        pydoc.pager(text)

    def open(self, url: str) -> None:
        subprocess.run(["open", url], check=False)


class _Quit(Exception):
    pass


@dataclass(frozen=True)
class _Entry:
    item: ReviewItem
    message: InboxMessage

    @property
    def received(self) -> date:
        return self.message.received_at.astimezone(MALAYSIA).date()

    @property
    def sender_name(self) -> str | None:
        name = _line(parseaddr(self.message.sender)[0])
        return name or None


def _line(text: str) -> str:
    return " ".join(_UNSAFE.sub("", text).split())


def _shorten(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(width - 1, 0)] + "…"


def _why(item: ReviewItem) -> str:
    if item.missing_fields:
        names = ", ".join(FIELD_NAMES.get(field, field) for field in item.missing_fields)
        return f"missing {names}"
    return _line(item.reason)


def _entries(store: Store) -> list[_Entry]:
    return [
        _Entry(item, store.message(item.provider, item.message_id))
        for item in store.list_review_items()
    ]


def render_list(entries: list[_Entry], columns: int) -> str:
    """Number each review item on one line: date, sender, subject, and why it waits."""
    count = len(entries)
    lines = [f"{count} message{'' if count == 1 else 's'} to review", ""]
    number_width = len(str(count))
    senders = [
        _shorten(entry.sender_name or _line(parseaddr(entry.message.sender)[1]), SENDER_WIDTH)
        for entry in entries
    ]
    whys = [_shorten(_why(entry.item), WHY_WIDTH) for entry in entries]
    sender_width = max(len(sender) for sender in senders)
    used = 2 + number_width + 2 + 10 + 2 + sender_width + 2 + 2 + max(len(why) for why in whys)
    subjects = [_line(entry.message.subject) for entry in entries]
    subject_width = min(max(len(subject) for subject in subjects), max(columns - used, 10))
    for number, (entry, sender, subject, why) in enumerate(
        zip(entries, senders, subjects, whys), start=1
    ):
        received = entry.received
        lines.append(
            f"  {number:>{number_width}}  {received:%a} {received.day:>2} {received:%b}  "
            f"{sender:<{sender_width}}  {_shorten(subject, subject_width):<{subject_width}}  {why}"
        )
    return "\n".join(lines)


def render_item(entry: _Entry) -> str:
    message, item = entry.message, entry.item
    received = message.received_at.astimezone(MALAYSIA)
    missing = ", ".join(FIELD_NAMES.get(field, field) for field in item.missing_fields)
    lines = [
        _line(message.subject) or "(no subject)",
        f"  From      {_line(message.sender)}",
        f"  Received  {received:%Y-%m-%d %H:%M}",
        f"  Reason    {_line(item.reason)}",
        f"  Missing   {missing or 'none'}",
        "",
    ]
    text = [line for line in message.content.text.splitlines() if line.strip()]
    lines.extend(f"    {_UNSAFE.sub('', line).rstrip()}" for line in text[:PREVIEW_LINES])
    if not text:
        lines.append("    (no text)")
    elif len(text) > PREVIEW_LINES:
        more = len(text) - PREVIEW_LINES
        lines.append(f"    … {more} more line{'' if more == 1 else 's'}, m shows the whole text")
    return "\n".join(lines)


def parse_merchant(text: str) -> str:
    if not text.strip():
        raise ValueError("The merchant can't be blank")
    return text.strip()


def parse_currency(text: str) -> str:
    code = text.strip().upper()
    try:
        if len(code) != 3 or not code.isascii() or not code.isalpha():
            raise ValueError
        Currency(code)
    except ValueError:
        raise ValueError(
            f"{text.strip()!r} is not an ISO 4217 currency code, such as MYR"
        ) from None
    return code


def _exponent(currency: str) -> int:
    return Currency(currency).exponent or 0


def amount_example(currency: str) -> str:
    exponent = _exponent(currency)
    return "1250" if exponent == 0 else "12." + "5".ljust(exponent, "0")


def parse_amount(text: str, currency: str) -> int:
    """Convert an amount typed in the currency's major unit, such as 12.50, to minor units."""
    exponent = _exponent(currency)
    match = _AMOUNT.fullmatch(text.strip())
    if match is None:
        raise ValueError(
            f"Type the amount as a receipt shows it, such as {amount_example(currency)}"
        )
    decimals = match.group(2) or ""
    if len(decimals) > exponent:
        places = f"at most {exponent} decimal places" if exponent else "no decimal places"
        raise ValueError(f"{currency} amounts have {places}")
    units = int(match.group(1).replace(",", ""))
    amount = units * 10**exponent + int(decimals.ljust(exponent, "0") or 0)
    if amount <= 0:
        raise ValueError("The amount must be more than zero")
    return amount


def parse_date(text: str) -> date:
    try:
        if len(text.strip()) != 10:
            raise ValueError
        return date.fromisoformat(text.strip())
    except ValueError:
        raise ValueError("Type the date as YYYY-MM-DD") from None


class _Review:
    def __init__(self, store: Store, console: Console) -> None:
        self.store = store
        self.console = console

    def ask(self, prompt: str) -> str:
        answer = self.console.ask(prompt)
        if answer.strip().lower() == "q":
            raise _Quit
        return answer

    def ask_value(self, label: str, default: str | None, parse: Callable[[str], T]) -> T:
        prompt = f"{label} [{default}]: " if default is not None else f"{label}: "
        while True:
            answer = self.ask(prompt)
            if not answer.strip() and default is not None:
                answer = default
            try:
                return parse(answer)
            except ValueError as error:
                self.console.write(f"  {error}.")

    def confirm(self, decision: str) -> bool:
        self.console.write(decision)
        while True:
            answer = self.ask("Save? y/n: ").strip().lower()
            if answer == "y":
                return True
            if answer == "n":
                self.console.write("Not saved.")
                return False

    def run(self) -> None:
        while True:
            entries = _entries(self.store)
            if not entries:
                self.console.write("Nothing to review.")
                return
            self.console.write(render_list(entries, self.console.width()))
            self.console.write()
            start = self.pick(len(entries))
            for number, entry in enumerate(entries[start:], start=start + 1):
                self.console.write()
                if not self.unresolved(entry):
                    self.console.write(f"Message {number} was already resolved; passing over it.")
                    continue
                self.resolve(entry)
            self.console.write()

    def pick(self, count: int) -> int:
        while True:
            answer = self.ask("Number to review, or q to quit: ").strip()
            if answer.isdigit() and 1 <= int(answer) <= count:
                return int(answer) - 1
            self.console.write(f"  Type a number from 1 to {count}.")

    def unresolved(self, entry: _Entry) -> bool:
        return any(
            item.analysis_id == entry.item.analysis_id for item in self.store.list_review_items()
        )

    def resolve(self, entry: _Entry) -> None:
        """Show one item and act on its keys until it is resolved or skipped."""
        self.console.write(render_item(entry))
        self.console.write()
        while True:
            key = self.ask(KEYS).strip().lower()
            if key == "e":
                transaction = self.ask_expense(entry)
                row = "  ".join(
                    expense_row(
                        transaction.transaction_date,
                        transaction.merchant,
                        transaction.amount_minor,
                        transaction.currency,
                    )
                )
                if self.confirm(f"Expense  {row}"):
                    self.save(entry, AnalysisDecision.EXPENSE, transaction)
                    return
            elif key == "n":
                if self.confirm("Not an expense"):
                    self.save(entry, AnalysisDecision.NOT_EXPENSE, None)
                    return
            elif key == "m":
                lines = entry.message.content.text.splitlines()
                self.console.page("".join(f"{_UNSAFE.sub('', line)}\n" for line in lines))
            elif key == "g":
                url = item_url(entry.item)
                if url is None:
                    self.console.write("  This message has no Gmail link.")
                else:
                    self.console.open(url)
            elif key == "s":
                return
            else:
                self.console.write("  Press e, n, m, g, s, or q.")

    def ask_expense(self, entry: _Entry) -> Transaction:
        self.console.write("Press Enter for the value in brackets.")
        merchant = self.ask_value("Merchant", entry.sender_name, parse_merchant)
        currency = self.ask_value("Currency", HOME_CURRENCY, parse_currency)
        amount = self.ask_value(
            f"Amount in {currency}, such as {amount_example(currency)}",
            None,
            lambda text: parse_amount(text, currency),
        )
        transaction_date = self.ask_value("Date", entry.received.isoformat(), parse_date)
        return Transaction(
            merchant=merchant,
            amount_minor=amount,
            currency=currency,
            transaction_date=transaction_date,
        )

    def save(
        self, entry: _Entry, decision: AnalysisDecision, transaction: Transaction | None
    ) -> None:
        try:
            self.store.resolve_review(
                entry.item.provider, entry.item.message_id, decision, transaction
            )
        except ReviewResolutionError:
            self.console.write("This message was already resolved elsewhere; nothing was saved.")
        else:
            self.console.write("Saved.")


def review(store: Store, console: Console) -> int:
    """Resolve the unresolved review items one at a time, as `review-resolve` would."""
    if not console.interactive():
        entries = _entries(store)
        console.write(render_list(entries, console.width()) if entries else "Nothing to review.")
        return 0
    try:
        _Review(store, console).run()
    except _Quit:
        pass
    except (EOFError, KeyboardInterrupt):
        console.write()
    return 0
