"""Subscriptions worked out from the active transactions each time (contract §10)."""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date

from .spending import HOME_CURRENCY, _day, _merchant, format_money, table, totals
from .storage import LedgerEntry

# The days allowed between consecutive charges, both inclusive, and the months one interval adds.
INTERVALS = (("monthly", 26, 35, 1), ("yearly", 350, 380, 12))


@dataclass(frozen=True)
class Subscription:
    merchant: str
    interval: str
    months: int
    latest: LedgerEntry
    previous: LedgerEntry
    second_charge: date
    next_charge: date
    lapsed: bool

    @property
    def price_changed(self) -> bool:
        latest, previous = self.latest, self.previous
        return (latest.amount_minor, latest.currency) != (previous.amount_minor, previous.currency)

    @property
    def monthly_minor(self) -> int:
        """The latest amount per month; a yearly one counts a twelfth, rounded half up."""
        return (self.latest.amount_minor * 2 + self.months) // (self.months * 2)


def merchant_key(name: str) -> str:
    """Case-fold a merchant name and collapse its whitespace; nothing fuzzier."""
    return " ".join(name.casefold().split())


def add_months(day: date, months: int) -> date:
    """The same day `months` later, clamped to the end of a shorter month."""
    year, month = divmod(day.month - 1 + months, 12)
    year, month = day.year + year, month + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _interval(charges: Sequence[LedgerEntry]) -> tuple[str, int] | None:
    gaps = [
        (later.transaction_date - earlier.transaction_date).days
        for earlier, later in zip(charges, charges[1:])
    ]
    for name, shortest, longest, months in INTERVALS:
        if all(shortest <= gap <= longest for gap in gaps):
            return name, months
    return None


def detect(entries: Sequence[LedgerEntry], today: date) -> list[Subscription]:
    """Find the merchants charged at least twice with every gap fitting one interval.

    The rest come first, ordered by their expected next charge, and lapsed ones last.
    """
    series: dict[str, list[LedgerEntry]] = {}
    for entry in entries:
        if entry.transaction_date <= today:
            series.setdefault(merchant_key(entry.merchant), []).append(entry)
    found = []
    for charges in series.values():
        charges.sort(key=lambda entry: entry.transaction_date)
        if len(charges) < 2 or (interval := _interval(charges)) is None:
            continue
        name, months = interval
        next_charge = add_months(charges[-1].transaction_date, months)
        found.append(
            Subscription(
                merchant=charges[-1].merchant,
                interval=name,
                months=months,
                latest=charges[-1],
                previous=charges[-2],
                second_charge=charges[1].transaction_date,
                next_charge=next_charge,
                lapsed=today > add_months(next_charge, months),
            )
        )
    return sorted(found, key=lambda item: (item.lapsed, item.next_charge, item.merchant))


def _this_month(day: date, today: date) -> bool:
    return (day.year, day.month) == (today.year, today.month)


def is_new(subscription: Subscription, today: date) -> bool:
    """Whether its second charge, the one that makes it a subscription, is this month."""
    return _this_month(subscription.second_charge, today)


def price_change(subscription: Subscription) -> str:
    """Describe how the latest charge differs from the one before, as `up MYR 2.00`."""
    latest, previous = subscription.latest, subscription.previous
    if latest.currency != previous.currency:
        return f"now {format_money(latest.amount_minor, latest.currency)}"
    difference = latest.amount_minor - previous.amount_minor
    direction = "up" if difference > 0 else "down"
    return f"{direction} {format_money(abs(difference), latest.currency)}"


def _flags(subscription: Subscription, today: date) -> list[str]:
    flags = [price_change(subscription)] if subscription.price_changed else []
    if is_new(subscription, today):
        flags.append("new this month")
    if subscription.lapsed:
        flags.append("lapsed?")
    return flags


def _date(day: date) -> str:
    return f"{day.day} {day:%b %Y}"


def render_subscriptions(subscriptions: Sequence[Subscription], today: date) -> str:
    """List each subscription with its flags, then the monthly total of those not lapsed."""
    lines = [f"Subscriptions · {_day(today)}", ""]
    if not subscriptions:
        lines.append("  None found. A merchant appears after its second regular charge.")
        return "\n".join(lines) + "\n"
    rows = [
        (
            _merchant(item.merchant),
            item.interval,
            f"last {_date(item.latest.transaction_date)}",
            f"next {_date(item.next_charge)}",
            format_money(item.latest.amount_minor, item.latest.currency),
        )
        for item in subscriptions
    ]
    for line, item in zip(table(rows), subscriptions):
        flags = _flags(item, today)
        lines.append(f"{line}  {', '.join(flags)}" if flags else line)
    active = [item for item in subscriptions if not item.lapsed]
    monthly = totals([replace(item.latest, amount_minor=item.monthly_minor) for item in active])
    amounts = " + ".join(format_money(amount, currency) for currency, amount in monthly)
    lines.extend(["", f"  Per month  {amounts or format_money(0, HOME_CURRENCY)}"])
    if any(item.interval == "yearly" for item in active):
        lines.append("  A yearly subscription counts as a twelfth of its amount.")
    if len(active) < len(subscriptions):
        lines.append("  A lapsed? subscription, overdue by more than an interval, is not counted.")
    if len(monthly) > 1:
        lines.append(f"  Each currency is totalled separately, not converted to {HOME_CURRENCY}.")
    return "\n".join(lines) + "\n"


def subscriptions_row(subscriptions: Sequence[Subscription], today: date) -> str | None:
    """The `status` row: price changes charged this month and new subscriptions, or None."""
    changed = [
        f"{_merchant(item.merchant)} {price_change(item)}"
        for item in subscriptions
        if item.price_changed and _this_month(item.latest.transaction_date, today)
    ]
    new = sum(is_new(item, today) for item in subscriptions)
    parts = [", ".join(changed)] if changed else []
    if new:
        parts.append(f"{new} new this month")
    return " · ".join(parts) or None
