from __future__ import annotations

from iso4217 import Currency

from .models import Transaction
from .review import (
    Console,
    Prompter,
    Quit,
    amount_example,
    parse_amount,
    parse_currency,
    parse_date,
    parse_merchant,
)
from .spending import Period, expense_row, render_spending, table
from .storage import LedgerCorrectionError, LedgerEntry, Store


def major_amount(amount_minor: int, currency: str) -> str:
    """Show an amount in minor units as it is typed at the amount prompt, such as `1234.50`."""
    exponent = Currency(currency).exponent or 0
    if exponent == 0:
        return str(amount_minor)
    units, minor = divmod(amount_minor, 10**exponent)
    return f"{units}.{minor:0{exponent}d}"


def _row(transaction: Transaction) -> tuple[str, str, str]:
    return expense_row(
        transaction.transaction_date,
        transaction.merchant,
        transaction.amount_minor,
        transaction.currency,
    )


class _Correction(Prompter):
    def __init__(self, store: Store, period: Period, console: Console) -> None:
        super().__init__(console)
        self.store = store
        self.period = period

    def run(self) -> None:
        while True:
            entries = self.store.list_transactions(self.period.start, self.period.end)
            listing = render_spending(self.period, entries, by_merchant=False, numbered=True)
            self.console.write(listing.rstrip("\n"))
            if not entries:
                return
            self.console.write()
            picked = self.pick(len(entries))
            if picked is None:
                return
            self.console.write()
            self.correct(entries[picked])
            self.console.write()

    def pick(self, count: int) -> int | None:
        while True:
            answer = self.ask("Number to correct, or Enter to quit: ").strip()
            if not answer:
                return None
            if answer.isdigit() and 1 <= int(answer) <= count:
                return int(answer) - 1
            self.console.write(f"  Type a number from 1 to {count}, or press Enter to quit.")

    def correct(self, entry: LedgerEntry) -> None:
        """Ask for each value with the logged one as its default, and save after confirmation."""
        previous = Transaction(
            merchant=entry.merchant,
            amount_minor=entry.amount_minor,
            currency=entry.currency,
            transaction_date=entry.transaction_date,
        )
        self.console.write("Correcting  " + "  ".join(_row(previous)))
        self.console.write("Press Enter for the value in brackets.")
        merchant = self.ask_value("Merchant", entry.merchant, parse_merchant)
        currency = self.ask_value("Currency", entry.currency, parse_currency)
        if currency == entry.currency:
            label, default = f"Amount in {currency}", major_amount(entry.amount_minor, currency)
        else:
            label, default = f"Amount in {currency}, such as {amount_example(currency)}", None
        amount = self.ask_value(label, default, lambda text: parse_amount(text, currency))
        transaction_date = self.ask_value("Date", entry.transaction_date.isoformat(), parse_date)
        corrected = Transaction(
            merchant=merchant,
            amount_minor=amount,
            currency=currency,
            transaction_date=transaction_date,
        )
        if corrected == previous:
            self.console.write("Nothing changed.")
            return
        rows = [("Was", *_row(previous)), ("Now", *_row(corrected))]
        if not self.confirm("\n".join(row.removeprefix("  ") for row in table(rows))):
            return
        try:
            self.store.correct_transaction(
                entry.provider,
                entry.message_id,
                merchant=corrected.merchant,
                amount_minor=corrected.amount_minor,
                currency=corrected.currency,
                transaction_date=corrected.transaction_date,
            )
        except LedgerCorrectionError:
            self.console.write("This expense is no longer logged; nothing was saved.")
        else:
            self.console.write("Saved.")


def correct_spending(store: Store, period: Period, console: Console) -> int:
    """List the period's expenses, numbered, and correct picked ones as `ledger-correct` would."""
    if not console.interactive():
        entries = store.list_transactions(period.start, period.end)
        console.write(render_spending(period, entries, by_merchant=False).rstrip("\n"))
        return 0
    try:
        _Correction(store, period, console).run()
    except Quit:
        pass
    except (EOFError, KeyboardInterrupt):
        console.write()
    return 0
