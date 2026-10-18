from __future__ import annotations

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from iso4217 import Currency

from .storage import LedgerEntry

HOME_CURRENCY = "MYR"
PERIOD_NAMES = ("today", "yesterday", "week", "last-week", "month", "last-month", "year")
MERCHANT_WIDTH = 32

_FAILED_STAGES = {
    "sign_in": "the Gmail sign-in",
    "runtime": "the analyser (Ollama)",
    "retrieval": "the Gmail search",
    "unexpected": "an unexpected error",
}


@dataclass(frozen=True)
class Period:
    label: str
    start: date
    end: date


def _day(value: date) -> str:
    return f"{value:%a} {value.day} {value:%b %Y}"


def _range(start: date, end: date) -> str:
    return f"{start:%a} {start.day} {start:%b} – {_day(end)}"


def _month(year: int, month: int) -> Period:
    last = calendar.monthrange(year, month)[1]
    label = f"{calendar.month_name[month]} {year}"
    return Period(label, date(year, month, 1), date(year, month, last))


def _year(year: int) -> Period:
    return Period(str(year), date(year, 1, 1), date(year, 12, 31))


def parse_period(value: str, today: date) -> Period:
    """Resolve a period name or a YYYY-MM-DD, YYYY-MM, or YYYY date against `today`."""
    week_start = today - timedelta(days=today.weekday())
    if value == "today":
        return Period(f"Today, {_day(today)}", today, today)
    if value == "yesterday":
        yesterday = today - timedelta(days=1)
        return Period(f"Yesterday, {_day(yesterday)}", yesterday, yesterday)
    if value in ("week", "last-week"):
        start = week_start if value == "week" else week_start - timedelta(days=7)
        end = start + timedelta(days=6)
        name = "This week" if value == "week" else "Last week"
        return Period(f"{name}, {_range(start, end)}", start, end)
    if value == "month":
        return _month(today.year, today.month)
    if value == "last-month":
        previous = today.replace(day=1) - timedelta(days=1)
        return _month(previous.year, previous.month)
    if value == "year":
        return _year(today.year)
    try:
        if len(value) == 10:
            day = date.fromisoformat(value)
            return Period(_day(day), day, day)
        if len(value) == 7 and value[4] == "-":
            return _month(int(value[:4]), int(value[5:]))
        if len(value) == 4:
            return _year(int(value))
    except ValueError:
        pass
    raise ValueError(
        f"period must be one of {', '.join(PERIOD_NAMES)}, "
        "or a date as YYYY-MM-DD, YYYY-MM, or YYYY"
    )


def format_money(amount_minor: int, currency: str) -> str:
    """Show an amount in minor units in its currency's major unit, such as `MYR 24.90`."""
    exponent = Currency(currency).exponent or 0
    if exponent == 0:
        return f"{currency} {amount_minor:,}"
    units, minor = divmod(amount_minor, 10**exponent)
    return f"{currency} {units:,}.{minor:0{exponent}d}"


def _currency_order(currency: str) -> tuple[bool, str]:
    return currency != HOME_CURRENCY, currency


def totals(entries: Sequence[LedgerEntry]) -> list[tuple[str, int]]:
    """Total the entries per currency, ringgit first; amounts are never converted."""
    sums: dict[str, int] = {}
    for entry in entries:
        sums[entry.currency] = sums.get(entry.currency, 0) + entry.amount_minor
    return sorted(sums.items(), key=lambda item: _currency_order(item[0]))


def _expenses(count: int) -> str:
    return f"{count} expense{'' if count == 1 else 's'}"


def summary_line(entries: Sequence[LedgerEntry]) -> str:
    if not entries:
        return f"{format_money(0, HOME_CURRENCY)} · no expenses"
    amounts = " + ".join(format_money(amount, currency) for currency, amount in totals(entries))
    return f"{amounts} · {_expenses(len(entries))}"


def _merchant(name: str) -> str:
    return name if len(name) <= MERCHANT_WIDTH else name[: MERCHANT_WIDTH - 1] + "…"


def _table(rows: list[tuple[str, ...]]) -> list[str]:
    """Left-align every column except the last, which holds right-aligned amounts."""
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    lines = []
    for row in rows:
        cells = [cell.ljust(width) for cell, width in zip(row[:-1], widths)]
        lines.append("  " + "  ".join([*cells, row[-1].rjust(widths[-1])]))
    return lines


def render_spending(period: Period, entries: Sequence[LedgerEntry], by_merchant: bool) -> str:
    lines = [period.label, ""]
    if not entries:
        lines.append("  No expenses logged.")
        return "\n".join(lines) + "\n"
    if by_merchant:
        groups: dict[tuple[str, str], list[int]] = {}
        for entry in entries:
            groups.setdefault((entry.merchant, entry.currency), []).append(entry.amount_minor)
        ordered = sorted(
            groups.items(),
            key=lambda item: (_currency_order(item[0][1]), -sum(item[1]), item[0][0]),
        )
        rows = [
            (_merchant(merchant), f"{len(amounts)}×", format_money(sum(amounts), currency))
            for (merchant, currency), amounts in ordered
        ]
    else:
        rows = [
            (
                f"{entry.transaction_date:%a} {entry.transaction_date.day:>2} "
                f"{entry.transaction_date:%b}",
                _merchant(entry.merchant),
                format_money(entry.amount_minor, entry.currency),
            )
            for entry in entries
        ]
    lines.extend(_table(rows))
    lines.extend(["", f"  Total  {summary_line(entries)}"])
    if len(totals(entries)) > 1:
        lines.append(f"  Each currency is totalled separately, not converted to {HOME_CURRENCY}.")
    return "\n".join(lines) + "\n"


def last_sync(log: Path) -> str | None:
    """Describe the newest scheduled run in the log that synced or failed."""
    if not log.is_file():
        return None
    for line in reversed(log.read_text(encoding="utf-8").splitlines()):
        started, _, rest = line.partition(" ")
        fields = dict(field.partition("=")[::2] for field in rest.split())
        if fields.get("outcome") not in ("synced", "failed"):
            continue
        try:
            when = datetime.fromisoformat(started)
        except ValueError:
            continue
        stamp = f"{when:%a} {when.day} {when:%b %H:%M}"
        if fields["outcome"] == "failed":
            stage = _FAILED_STAGES.get(fields.get("stage", ""), "an unexpected error")
            return f"{stamp} · stopped at {stage}"
        counts = [
            _expenses(int(fields.get("expense", 0))),
            f"{fields.get('needs_review', 0)} to review",
            f"{fields.get('failed', 0)} failed",
        ]
        return f"{stamp} · {', '.join(counts)}"
    return None


def render_status(
    today: date,
    entries: Sequence[LedgerEntry],
    review_count: int,
    sync: str | None,
) -> str:
    """Summarize today, this week, and this month from the month's and week's entries."""
    week = parse_period("week", today)
    month = parse_period("month", today)

    def within(period: Period) -> list[LedgerEntry]:
        return [entry for entry in entries if period.start <= entry.transaction_date <= today]

    review = "nothing" if review_count == 0 else f"{review_count} item{'s' * (review_count != 1)}"
    rows = [
        ("Today", summary_line(within(Period("", today, today)))),
        ("This week", summary_line(within(week))),
        ("This month", summary_line(within(month))),
        ("To review", review),
        ("Last sync", sync or "none recorded"),
    ]
    lines = [f"Mizu Kakeibo · {_day(today)}", ""]
    lines.extend(f"  {name:<11} {value}" for name, value in rows)
    lines.extend(
        [
            "",
            "  mizu-kakeibo spend month                 this month's expenses",
            "  mizu-kakeibo spend month --by merchant   this month by merchant",
            "  mizu-kakeibo review-open                 open the review list",
        ]
    )
    return "\n".join(lines) + "\n"
